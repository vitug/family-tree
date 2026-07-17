"""Аутентификация редактора по токену (X-Edit-Token заголовок)."""

from fastapi import HTTPException, Security, status
from starlette.requests import Request

from app.config import settings


def require_edit_token(request: Request) -> None:
    """Проверить, что токен редактирования совпадает с конфигом.

    Токен передаётся в заголовке X-Edit-Token.
    """
    token = request.headers.get("X-Edit-Token", "").strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется токен редактирования (X-Edit-Token)",
        )
    if token != settings.edit_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Неверный токен редактирования",
        )
