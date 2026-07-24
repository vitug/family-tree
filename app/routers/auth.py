from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

import logging

from app.config import settings
from app.security import rate_limit_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

security = HTTPBearer()

# ── Pydantic модели ──────────────────────────────────────────────────────

class TokenRequest(BaseModel):
    """Запрос на токен с view token или edit token."""
    token: str

class TokenResponse(BaseModel):
    """Ответ с JWT токеном."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # секунды до истечения
    role: str  # "viewer" или "editor"

class TokenRefreshRequest(BaseModel):
    """Запрос на обновление токена."""
    token: str

# ── JWT функции ───────────────────────────────────────────────────────────

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Создать JWT токен."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_access_token_expire_hours)
    
    to_encode.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "access"
    })
    
    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return encoded_jwt

def decode_access_token(token: str) -> dict:
    """Декодировать и валидировать JWT токен."""
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Токен истёк",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный токен",
            headers={"WWW-Authenticate": "Bearer"},
        )

# ── Dependencies ───────────────────────────────────────────────────────────

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """Получить текущего пользователя из JWT токена."""
    token = credentials.credentials
    payload = decode_access_token(token)
    
    # Проверка типа токена
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный тип токена",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Проверка роли
    role = payload.get("role")
    if role not in ["viewer", "editor"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверная роль пользователя",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return payload

async def require_jwt_token(
    current_user: dict = Depends(get_current_user)
) -> dict:
    """Требовать валидный JWT токен для доступа (минимальная роль: viewer)."""
    return current_user

async def require_editor_token(
    current_user: dict = Depends(get_current_user)
) -> dict:
    """Требовать валидный JWT токен с ролью editor для доступа."""
    if current_user.get("role") != "editor":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Требуются права редактирования (editor role)",
        )
    return current_user

# ── Legacy для обратной совместимости ───────────────────────────────────────

logger.info("Auth router initialized with JWT support")

def require_edit_token(request: Request) -> None:
    """Проверить, что токен редактирования совпадает с конфигом.
    
    ⚠️ Legacy функция! Рекомендуется использовать require_jwt_token.
    Токен передаётся в заголовке X-Edit-Token.
    """
    token = (request.headers.get("X-Edit-Token") or "").strip()
    logger.debug(
        "Checking edit token from headers: %s; has_edit_token=%s",
        "HIDDEN" if not token else token[:4] + "...",
        settings.edit_token[:4] + "...",
    )
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Требуется токен редактирования (X-Edit-Token)"
        )
    if token != settings.edit_token:
        logger.warning(
            "Edit token invalid: %s != %s", token[:4] + "...", settings.edit_token[:4] + "..."
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Неверный токен редактирования"
        )
    logger.debug("Edit token accepted")

@router.post("/verify-edit-token")
async def verify_edit_token(request: Request) -> dict[str, bool]:
    """Проверить токен редактирования, отправленный в заголовке X-Edit-Token.
    
    ⚠️ Legacy эндпоинт! Рекомендуется использовать /token.
    """
    logger.info("verify-edit-token called")
    token = (request.headers.get("X-Edit-Token", "") or "").strip()
    valid = token == settings.edit_token
    logger.debug("Token validation result: %s (token provided: %s)", valid, bool(token))
    if not valid and token:
        # token provided but wrong
        logger.warning("Invalid edit token provided")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Неверный токен редактирования"
        )
    return {"valid": valid}

# ── JWT эндпоинты ─────────────────────────────────────────────────────────

@router.post("/token", response_model=TokenResponse, dependencies=[Depends(rate_limit_auth)])
async def login_with_token(request: Request, request_data: TokenRequest) -> TokenResponse:
    """Получить JWT токен в обмен на view token или edit token.

    - view token даёт роль "viewer" (только чтение)
    - edit token даёт роль "editor" (чтение и запись)

    Защищено от brute-force: rate limiting + временная блокировка.
    """
    from app.security import check_brute_force

    logger.info("Login attempt")
    logger.info(f"Received token: {repr(request_data.token)}")
    logger.info(f"View token: {repr(settings.view_token)}")
    logger.info(f"Edit token: {repr(settings.edit_token)}")

    # Brute-force проверка: заблокирован ли этот IP?
    await check_brute_force(request, success=False)  # Пока не знаем успех

    # Удаляем лишние пробелы из настроек
    clean_view_token = settings.view_token.strip()
    clean_edit_token = settings.edit_token.strip()
    clean_user_token = request_data.token.strip()

    # Проверка токена и определение роли
    role = None
    if clean_user_token == clean_view_token:
        role = "viewer"
        logger.info("Login successful with view token")
    elif clean_user_token == clean_edit_token:
        role = "editor"
        logger.info("Login successful with edit token")
    else:
        logger.warning("Invalid token for login")
        logger.warning(
            f"Token comparison failed: {repr(clean_user_token)} != {repr(clean_view_token)} and {repr(clean_user_token)} != {repr(clean_edit_token)}"
        )
        # Фиксируем неудачную попытку для anti-brute-force
        await check_brute_force(request, success=False)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный токен авторизации",
        )

    # Успешная попытка: очищаем brute-force счётчик
    await check_brute_force(request, success=True)

    # Создание JWT токена с ролью
    access_token = create_access_token(data={"sub": "user", "role": role})

    # Расчет времени жизни токена в секундах
    expires_in = settings.jwt_access_token_expire_hours * 3600

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=expires_in,
        role=role,
    )

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request_data: TokenRefreshRequest) -> TokenResponse:
    """Обновить JWT токен (удлинить истечение) с сохранением роли."""
    try:
        # Проверяем старый токен
        payload = decode_access_token(request_data.token)
        
        if payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Неверный тип токена"
            )
        
        role = payload.get("role")
        logger.info(f"Token refresh for user with role: {role}")
        
        # Создаем новый токен с той же ролью
        access_token = create_access_token(data={"sub": "user", "role": role})
        expires_in = settings.jwt_access_token_expire_hours * 3600
        
        return TokenResponse(
            access_token=access_token,
            token_type="bearer",
            expires_in=expires_in,
            role=role
        )
        
    except HTTPException:
        # Пробрасываем дальше
        raise
    except Exception as e:
        logger.error(f"Error refreshing token: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Не удалось обновить токен"
        )

@router.get("/me")
async def get_current_user_info(
    current_user: dict = Depends(require_jwt_token)
) -> dict:
    """Получить информацию о текущем пользователе."""
    return {
        "user": "user",
        "role": current_user.get("role"),
        "permissions": "read_write" if current_user.get("role") == "editor" else "read_only",
        "exp": current_user.get("exp"),
        "iat": current_user.get("iat")
    }
