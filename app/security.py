"""
Политики безопасности: rate limiting, brute-force защита, проверка доступа к медиа.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.database import get_db
from app.models import Media, Person

logger = logging.getLogger(__name__)

# ── Rate limiting (in-memory) ───────────────────────────────────────────────
# В продакшене при нескольких worker'ах используйте Redis.


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: dict[str, list[float]] = defaultdict(list)

    async def is_allowed(self, key: str, request: Request) -> bool:
        now = time.time()
        self.requests[key] = [t for t in self.requests[key] if now - t < self.window_seconds]

        if len(self.requests[key]) >= self.max_requests:
            logger.warning(
                "Rate limit exceeded for %s: %d/%d per %ds",
                key,
                len(self.requests[key]),
                self.max_requests,
                self.window_seconds,
            )
            return False

        self.requests[key].append(now)
        return True


AUTH_RATE_LIMITER = RateLimiter(max_requests=5, window_seconds=60)
API_RATE_LIMITER = RateLimiter(max_requests=100, window_seconds=60)


async def rate_limit_auth(request: Request) -> None:
    """Защита /auth/token от перебора."""
    if not settings.enable_rate_limiting:
        return
    client_ip = _get_client_ip(request)
    if not await AUTH_RATE_LIMITER.is_allowed(f"auth:{client_ip}", request):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много попыток входа. Попробуйте через минуту.",
            headers={"Retry-After": "60"},
        )


# ── Brute-force detection & temporary lockout ───────────────────────────────


class BruteForceProtection:
    def __init__(self, max_attempts: int = 3, lockout_seconds: int = 300):
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self.attempts: dict[str, list[float]] = defaultdict(list)
        self.lockouts: dict[str, float] = {}

    def record_attempt(self, key: str, success: bool = False) -> None:
        now = time.time()
        self.attempts[key] = [t for t in self.attempts[key] if now - t < self.lockout_seconds * 2]

        if success:
            self.attempts[key].clear()
            self.lockouts.pop(key, None)
            logger.info("Authentication success for %s, attempts cleared", key)
        else:
            self.attempts[key].append(now)
            if len(self.attempts[key]) >= self.max_attempts:
                unlock_time = now + self.lockout_seconds
                self.lockouts[key] = unlock_time
                logger.warning(
                    "Too many failed attempts for %s, locked until %s",
                    key,
                    datetime.fromtimestamp(unlock_time, tz=timezone.utc).isoformat(),
                )

    def is_locked(self, key: str) -> bool:
        now = time.time()
        unlock_time = self.lockouts.get(key)
        if unlock_time and now < unlock_time:
            remaining = int(unlock_time - now)
            logger.warning("Client %s is locked for %d more seconds", key, remaining)
            return True
        if unlock_time and now >= unlock_time:
            del self.lockouts[key]
        return False


BRUTE_PROTECTOR = BruteForceProtection(max_attempts=3, lockout_seconds=300)


async def ensure_not_locked(request: Request) -> None:
    """Только проверка блокировки (без записи попытки)."""
    if not settings.enable_brute_force_protection:
        return
    client_ip = _get_client_ip(request)
    key = f"ip:{client_ip}"
    if BRUTE_PROTECTOR.is_locked(key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Слишком много неудачных попыток. Попробуйте через несколько минут.",
        )


async def record_login_result(request: Request, success: bool) -> None:
    """Записать результат попытки входа."""
    if not settings.enable_brute_force_protection:
        return
    client_ip = _get_client_ip(request)
    BRUTE_PROTECTOR.record_attempt(f"ip:{client_ip}", success=success)


async def check_brute_force(request: Request, success: bool = False) -> None:
    """
    Совместимость со старым API.
    Предпочтительнее: ensure_not_locked + record_login_result.
    """
    if not settings.enable_brute_force_protection:
        return
    client_ip = _get_client_ip(request)
    key = f"ip:{client_ip}"
    if not success and BRUTE_PROTECTOR.is_locked(key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Слишком много неудачных попыток. Попробуйте через несколько минут.",
        )
    BRUTE_PROTECTOR.record_attempt(key, success)


# ── Timing-safe token compare ───────────────────────────────────────────────


def constant_time_token_match(a: str, b: str) -> bool:
    """Сравнение токенов без утечки по времени."""
    if a is None or b is None:
        return False
    return tokens_equal(a, b)


def tokens_equal(provided: str, expected: str) -> bool:
    """Безопасное сравнение токенов (устойчиво к разной длине)."""
    if provided is None or expected is None:
        return False
    p = provided.strip().encode("utf-8")
    e = expected.strip().encode("utf-8")
    if len(p) != len(e):
        # одинаковое время: сравниваем дайджесты, затем возвращаем False
        secrets.compare_digest(hashlib.sha256(p).digest(), hashlib.sha256(e).digest())
        return False
    return secrets.compare_digest(p, e)


# ── Media access protection ─────────────────────────────────────────────────


def _safe_media_path(relative: str) -> Path:
    """Разрешить относительный путь внутри media_dir без path traversal."""
    rel = relative.replace("\\", "/").lstrip("/")
    if ".." in rel.split("/"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Недопустимый путь")
    base = Path(settings.media_dir).resolve()
    full = (base / rel).resolve()
    if not str(full).startswith(str(base)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Недопустимый путь")
    return full


async def get_media_access(
    person_id: int,
    media_filename: str,
    db: Session,
) -> tuple[Person, Media | None]:
    """Проверка существования человека и (опционально) записи Media."""
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Человек не найден")

    safe_name = Path(media_filename).name
    stmt = (
        select(Media)
        .where(Media.person_id == person_id)
        .where(Media.file_path.like(f"%{safe_name}"))
        .order_by(Media.id)
    )
    media = db.scalars(stmt).first()
    return person, media


async def serve_protected_media(person_id: int, filename: str, db: Session) -> FileResponse:
    """Раздача файла media/files/{person_id}/{filename}."""
    await get_media_access(person_id, filename, db)

    safe_name = Path(filename).name
    candidates = [
        Path(settings.media_dir) / "files" / str(person_id) / safe_name,
        Path(settings.media_dir) / "files" / safe_name,
    ]
    media_path = next((p for p in candidates if p.is_file()), None)
    if media_path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не найден на диске")

    return FileResponse(media_path)


async def serve_protected_media_by_path(
    file_path: str,
    request: Request,
    db: Session,
) -> FileResponse:
    """
    Раздача по относительному пути (files/...), с JWT через Bearer или ?access_token=.
    """
    await require_media_auth(request)

    rel = file_path.replace("\\", "/").lstrip("/")
    if rel.startswith("media/"):
        rel = rel[len("media/") :]

    full = _safe_media_path(rel)
    if not full.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не найден на диске")

    return FileResponse(full)


async def require_media_auth(request: Request) -> dict:
    """JWT из Authorization: Bearer или query access_token (для img)."""
    token: str | None = None
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        token = request.query_params.get("access_token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация для доступа к медиа",
            headers={"WWW-Authenticate": "Bearer"},
        )

    from app.routers.auth import decode_access_token

    payload = decode_access_token(token)
    if payload.get("role") not in ("viewer", "editor"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
    return payload


# ── OpenAPI docs protection ─────────────────────────────────────────────────


async def require_docs_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
) -> bool:
    """Требует JWT токен для доступа к /docs и /openapi.json."""
    if not settings.protect_api_docs:
        return True
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация для доступа к документации API",
            headers={"WWW-Authenticate": "Bearer"},
        )

    from app.routers.auth import decode_access_token

    try:
        payload = decode_access_token(credentials.credentials)
        if payload.get("role") not in ("viewer", "editor"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Недостаточно прав для доступа к документации",
            )
        return True
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный токен для доступа к документации",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── Client IP extraction ───────────────────────────────────────────────────


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()

    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()

    if request.client and request.client.host:
        return request.client.host

    return "unknown"


# ── Utils ───────────────────────────────────────────────────────────────────


def validate_token_strength(token: str) -> tuple[bool, str]:
    token = token.strip()
    if len(token) < 12:
        return False, "Токен слишком короткий (минимум 12 символов)"
    if token in ("family", "secret", "password", "123456", "familyvitug"):
        return False, "Использовать стандартный/слабый токен небезопасно"
    if token.isalpha() or token.isdigit():
        return False, "Токен должен содержать смешанный алфавит"
    return True, "OK"


# ── Security headers middleware ────────────────────────────────────────────


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if not settings.enable_security_headers:
            return response

        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "font-src 'self'; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        return response
