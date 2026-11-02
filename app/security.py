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
        timestamps = [t for t in self.requests.get(key, ()) if now - t < self.window_seconds]
        if timestamps:
            self.requests[key] = timestamps
        else:
            # чистим пустые ключи: иначе defaultdict растёт без границы
            self.requests.pop(key, None)

        if len(timestamps) >= self.max_requests:
            logger.warning(
                "Rate limit exceeded for %s: %d/%d per %ds",
                key,
                len(timestamps),
                self.max_requests,
                self.window_seconds,
            )
            return False

        timestamps.append(now)
        self.requests[key] = timestamps
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
        attempts = [t for t in self.attempts.get(key, ()) if now - t < self.lockout_seconds * 2]

        if success:
            self.attempts.pop(key, None)
            self.lockouts.pop(key, None)
            logger.info("Authentication success for %s, attempts cleared", key)
        else:
            attempts.append(now)
            if len(attempts) >= self.max_attempts:
                unlock_time = now + self.lockout_seconds
                self.lockouts[key] = unlock_time
                logger.warning(
                    "Too many failed attempts for %s, locked until %s",
                    key,
                    datetime.fromtimestamp(unlock_time, tz=timezone.utc).isoformat(),
                )
            if attempts:
                self.attempts[key] = attempts
            else:
                self.attempts.pop(key, None)

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


# 5 попыток (было 3): легитимный пользователь с опечатками не запирается на 5 минут
BRUTE_PROTECTOR = BruteForceProtection(max_attempts=5, lockout_seconds=300)


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
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Недопустимый путь") from None
    base = Path(settings.media_dir).resolve()
    full = (base / rel).resolve()
    try:
        # каноничная проверка принадлежности (вместо prefix-матчинга строк,
        # который теоретически пропускал бы media2 при базе media)
        full.relative_to(base)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Недопустимый путь") from None
    return full


def unlink_media_file(relative_path: str | None) -> None:
    """Удалить файл с диска по относительному пути внутри media_dir."""
    if not relative_path:
        return
    try:
        full = _safe_media_path(relative_path)
        if full.is_file():
            full.unlink()
            logger.info("Deleted media file: %s", full)
        # миниатюра t_<stem>.jpg живёт рядом — чистим и её, иначе копится мусор
        thumb = full.with_name(f"t_{full.stem}.jpg")
        if thumb != full and thumb.is_file():
            thumb.unlink(missing_ok=True)
            logger.info("Deleted thumbnail: %s", thumb)
    except HTTPException:
        logger.warning("Skip delete, unsafe or missing path: %s", relative_path)
    except OSError as exc:
        logger.warning("Failed to delete media file %s: %s", relative_path, exc)


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


async def serve_protected_media(person_id: int, filename: str, db) -> FileResponse:
    """Раздача файла media/files/{person_id}/{filename}.

    db может быть None (запись уже проверена вызывающим кодом) —
    тогда поиск в БД пропускается: FileResponse стримится после закрытия
    сессии, чтобы не занимать соединение пула.
    """
    if db is not None:
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
) -> FileResponse:
    """
    Раздача по относительному пути (files/...), JWT через Bearer или httpOnly cookie.
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
    """JWT из Authorization: Bearer или httpOnly cookie (не из query — не светим в логах)."""
    from app.routers.auth import AUTH_COOKIE_NAME, decode_access_token

    token: str | None = None
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        token = (request.cookies.get(AUTH_COOKIE_NAME) or "").strip() or None

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация для доступа к медиа",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(token)
    if payload.get("role") not in ("viewer", "editor"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
    return payload


# ── OpenAPI docs protection ─────────────────────────────────────────────────


async def require_docs_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
) -> bool:
    """Требует JWT (Bearer или cookie) для /docs и /openapi.json."""
    if not settings.protect_api_docs:
        return True

    from app.routers.auth import AUTH_COOKIE_NAME, decode_access_token

    token: str | None = None
    if credentials and credentials.credentials:
        token = credentials.credentials
    if not token:
        token = (request.cookies.get(AUTH_COOKIE_NAME) or "").strip() or None

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация для доступа к документации API",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_access_token(token)
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
        ) from None


# ── Client IP extraction ───────────────────────────────────────────────────


def _get_client_ip(request: Request) -> str:
    # Заголовкам прокси верим только при явном FAMILY_TREE_TRUST_PROXY=true.
    # Иначе подмена X-Forwarded-For давала «новый IP» на каждый запрос
    # и обходила rate limit и brute-force lockout.
    if settings.trust_proxy:
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
    if token in ("family", "secret", "password", "123456", "familytree123"):
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
        response.headers["X-Content-Type-Options"] = "nosniff"
        # X-XSS-Protection удалён: устаревший (браузеры игнорируют/отключён),
        # а включённый мог включать XSS-аудитор в старых Chrome. CSP закрывает вопрос.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            # 'unsafe-inline' в script-src — компромисс SPA без сборки; при переходе
            # на сборку/nonce убрать. style-src inline нужен для инлайн-стилей узлов.
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "font-src 'self'; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "object-src 'none'; "
            "base-uri 'self';"
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        return response
