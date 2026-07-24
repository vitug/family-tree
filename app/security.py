"""
Политики безопасности: rate limiting, brute-force защита, проверка доступа к медиа.

Для активации rate limiting установите: pip install slowapi
"""

import logging
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Media, Person

logger = logging.getLogger(__name__)

# ── Rate limiting (in-memory) ───────────────────────────────────────────────
# В продакшене лучше использовать Redis, но для MVP достаточно in-memory

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


#.rate limiter для эндпоинта /auth/token
AUTH_RATE_LIMITER = RateLimiter(max_requests=5, window_seconds=60)  # 5 попыток в минуту

# Общий rate limiter для API
API_RATE_LIMITER = RateLimiter(max_requests=100, window_seconds=60)


async def rate_limit_auth(request: Request) -> None:
    """Middleware для защиты /auth/token от перебора."""
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
        self.lockouts: dict[str, float] = {}  # ip -> unlock_time

    def record_attempt(self, key: str, success: bool = False) -> None:
        now = time.time()
        # Очистка старых записей (более lockout_seconds)
        self.attempts[key] = [t for t in self.attempts[key] if now - t < self.lockout_seconds * 2]

        if success:
            self.attempts[key].clear()
            if key in self.lockouts:
                del self.lockouts[key]
            logger.info("Authentication success for %s,_attempts cleared", key)
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
        # Автоматически разблокируем, если время прошло
        if unlock_time and now >= unlock_time:
            del self.lockouts[key]
        return False


BRUTE_PROTECTOR = BruteForceProtection(max_attempts=3, lockout_seconds=300)  # 3 попытки, 5 минут


async def check_brute_force(request: Request, success: bool = False) -> None:
    """Проверка brute-force и временная блокировка."""
    client_ip = _get_client_ip(request)
    key = f"ip:{client_ip}"

    if not success:
        if BRUTE_PROTECTOR.is_locked(key):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Слишком много неудачных попыток. Попробуйте через несколько минут.",
            )

    BRUTE_PROTECTOR.record_attempt(key, success)


# ── Media access protection ─────────────────────────────────────────────────

async def get_media_access(
    person_id: int,
    media_filename: str,
    db: Session = Depends(get_db),
) -> tuple[Person, Media]:
    """
    Проверка доступа к медиа файлу.
    Используется вместо старого StaticFiles для /media/{person_id}/{filename}
    """
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Человек не найден")

    media = db.execute(
        Media.media_id, Media.person_id, Media.file_path
    ).filter(Media.person_id == person_id).filter(Media.file_path.like(f"%{media_filename}%")).order_by(Media.media_id).first()

    if not media:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Медиа файл не найден")

    return person, media


async def serve_protected_media(person_id: int, filename: str, db: Session = Depends(get_db)):
    """Эндпоинт для защищённой раздачи медиа."""
    person, media = await get_media_access(person_id, filename, db)

    media_base = Path(settings.media_dir) / "files" / str(person_id)
    media_path = media_base / filename

    if not media_path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не найден на диске")

    from fastapi.responses import FileResponse
    return FileResponse(media_path)


# ── OpenAPI docs protection ─────────────────────────────────────────────────

async def require_docs_auth(
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer(auto_error=False))
) -> bool:
    """Требует JWT токен для доступа к /docs и /openapi.json."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация для доступа к документации API",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    from app.routers.auth import decode_access_token

    try:
        payload = decode_access_token(token)
        if payload.get("role") not in ["viewer", "editor"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Недостаточно прав для доступа к документации",
            )
        return True
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный токен для доступа к документации",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── Client IP extraction ───────────────────────────────────────────────────

def _get_client_ip(request: Request) -> str:
    """
    Извлечение IP клиента с учетом прокси.
    В продакшене настройте Nginx/traefik на передачу X-Real-IP или X-Forwarded-For.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Может содержать несколько IP, берём первый (клиент)
        return forwarded.split(",")[0].strip()

    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()

    # Fallback на remote address
    if request.client and request.client.host:
        return request.client.host

    return "unknown"


# ── Decentralized security utils ───────────────────────────────────────────

def validate_token_strength(token: str) -> tuple[bool, str]:
    """
    Проверка сложности токена авторизации.
    Возвращает (valid, message).
    """
    token = token.strip()
    if len(token) < 12:
        return False, "Токен слишком короткий (минимум 12 символов)"
    if token in ["family", "secret", "password", "123456"]:
        return False, "Использовать стандартный токен небезопасно"
    if token.isalpha() or token.isdigit():
        return False, "Токен должен содержать хотя бы одну букву и цифру"
    return True, "OK"


def is_static_file_access(path: str) -> bool:
    """
    Для внутреннего использования middleware.
    Определяет, является ли запрос к статическому файлу.
    """
    return path.startswith("/static/") or path.startswith("/media/")


# ── Security headers middleware ────────────────────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Добавляет заголовки безопасности ко всем ответам."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Запрещаем фреймы (clickjacking защита)
        response.headers["X-Frame-Options"] = "DENY"

        # Включаем XSS фильтр браузера
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Запрещаем MIME-sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # CSP (Content Security Policy) - строгая политика для нашего SPA
        response.headers[
            "Content-Security-Policy"
        ] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; frame-ancestors 'none';"

        # Referrer-Policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Permissions-Policy (устанавливает доступные возможности браузера)
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        return response
