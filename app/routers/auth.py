from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict
from starlette.requests import Request

import logging

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

security = HTTPBearer()

# ── Pydantic модели ──────────────────────────────────────────────────────

class TokenRequest(BaseModel):
    """Запрос на токен с legacy edit token."""
    edit_token: str

class TokenResponse(BaseModel):
    """Ответ с JWT токеном."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # секунды до истечения

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
    
    return payload

async def require_jwt_token(
    current_user: dict = Depends(get_current_user)
) -> dict:
    """Требовать валидный JWT токен для доступа."""
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

@router.post("/token", response_model=TokenResponse)
async def login_with_token(request_data: TokenRequest) -> TokenResponse:
    """Получить JWT токен в обмен на edit token.
    
    Client должен отправить edit token и получить JWT токен для дальнейших запросов.
    """
    logger.info("Login attempt")
    
    # Проверка edit token
    if request_data.edit_token != settings.edit_token:
        logger.warning("Invalid edit token for login")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный токен редактирования"
        )
    
    logger.info("Login successful")
    
    # Создание JWT токена
    access_token = create_access_token(data={"sub": "user"})
    
    # Расчет времени жизни токена в секундах
    expires_in = settings.jwt_access_token_expire_hours * 3600
    
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=expires_in
    )

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request_data: TokenRefreshRequest) -> TokenResponse:
    """Обновить JWT токен (удлинить истечение)."""
    try:
        # Проверяем старый токен
        payload = decode_access_token(request_data.token)
        
        if payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Неверный тип токена"
            )
        
        logger.info("Token refresh for user")
        
        # Создаем новый токен
        access_token = create_access_token(data={"sub": "user"})
        expires_in = settings.jwt_access_token_expire_hours * 3600
        
        return TokenResponse(
            access_token=access_token,
            token_type="bearer",
            expires_in=expires_in
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
        "user": "admin",
        "sub": current_user.get("sub"),
        "exp": current_user.get("exp"),
        "iat": current_user.get("iat")
    }
