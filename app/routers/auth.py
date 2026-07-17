from fastapi import APIRouter, Depends, Security, status
from starlette.requests import Request

from app.config import settings
from app.database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


def require_edit_token(request: Request) -> None:
    """Проверить, что токен редактирования совпадает с конфигом.

    Токен передаётся в заголовке X-Edit-Token.
    """
    token = request.headers.get("X-Edit-Token", "").strip()
    if not token:
        raise status.HTTP_401_UNAUTHORIZED(detail="Требуется токен редактирования (X-Edit-Token)")
    if token != settings.edit_token:
        raise status.HTTP_403_FORBIDDEN(detail="Неверный токен редактирования")


@router.post("/verify-edit-token")
async def verify_edit_token(request: Request) -> dict[str, bool]:
    """Проверить токен редактирования, отправленный в заголовке X-Edit-Token."""
    token = (request.headers.get("X-Edit-Token", "") or "").strip()
    valid = token == settings.edit_token
    if not valid and token:
        # token provided but wrong
        raise status.HTTP_403_FORBIDDEN(detail="Неверный токен редактирования")
    return {"valid": valid}
