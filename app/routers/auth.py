from fastapi import APIRouter, Depends, Security, status, HTTPException
from starlette.requests import Request

import logging

from app.config import settings
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

logger = logging.getLogger(__name__)
logger.info("Auth router initialized with prefix=/auth")


def require_edit_token(request: Request) -> None:
    """Проверить, что токен редактирования совпадает с конфигом.

    Токен передаётся в заголовке X-Edit-Token.
    """
    token = (request.headers.get("X-Edit-Token") or "").strip()
    logger.debug(
        "Checking edit token from headers: %s; has_edit_token=%s",
        "HIDDEN" if not token else token[:4] + "...",
        settings.edit_token[:4] + "...",
    )
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется токен редактирования (X-Edit-Token)")
    if token != settings.edit_token:
        logger.warning(
            "Edit token invalid: %s != %s", token[:4] + "...", settings.edit_token[:4] + "..."
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Неверный токен редактирования")
    logger.debug("Edit token accepted")


@router.post("/verify-edit-token")
async def verify_edit_token(request: Request) -> dict[str, bool]:
    """Проверить токен редактирования, отправленный в заголовке X-Edit-Token."""
    logger.info("verify-edit-token called")
    token = (request.headers.get("X-Edit-Token", "") or "").strip()
    valid = token == settings.edit_token
    logger.debug("Token validation result: %s (token provided: %s)", valid, bool(token))
    if not valid and token:
        # token provided but wrong
        logger.warning("Invalid edit token provided")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Неверный токен редактирования")
    return {"valid": valid}
