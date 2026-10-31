from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

import logging
from types import SimpleNamespace

from app.config import settings
from app.security import rate_limit_auth, ensure_not_locked, record_login_result, tokens_equal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# HttpOnly cookie — JS не читает токен; <img> same-origin отправляет cookie автоматически
AUTH_COOKIE_NAME = "ft_access_token"


def _cookie_path() -> str:
    """Путь cookie = путь двери (root_path). Прод и dev-стенд живут на одном
    домене: без этого вход в один инстанс затирает cookie другого (разные
    JWT-секреты → 401 «слетела авторизация»)."""
    return settings.root_path or "/"


def set_auth_cookie(response: Response, request: Request, token: str, max_age: int) -> None:
    # Затереть легаси-cookie с Path=/ (до фикса пути): при дубле имени в
    # Cookie-заголовке Starlette оставляет ПОСЛЕДНИЙ, и старый прод-JWT
    # перебивает свежий cookie этого инстанса («просит авторизацию»).
    if _cookie_path() != "/":
        response.delete_cookie(
            key=AUTH_COOKIE_NAME,
            path="/",
            httponly=True,
            samesite="lax",
            secure=_cookie_secure(request),
        )
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        max_age=max_age,
        path=_cookie_path(),
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
    )


def clear_auth_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        key=AUTH_COOKIE_NAME,
        path=_cookie_path(),
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
    )
security_optional = HTTPBearer(auto_error=False)


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
    """Опциональный body для API-клиентов; SPA обновляет сессию по cookie."""
    token: Optional[str] = None


# ── Cookie helpers ─────────────────────────────────────────────────────────

def _cookie_secure(request: Request) -> bool:
    """Secure только по HTTPS (localhost HTTP без Secure)."""
    if request.url.scheme == "https":
        return True
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return forwarded == "https"


def extract_access_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = None,
) -> Optional[str]:
    """JWT из Authorization: Bearer или httpOnly cookie (не из query)."""
    if credentials and credentials.credentials:
        return credentials.credentials.strip()
    cookie = request.cookies.get(AUTH_COOKIE_NAME)
    if cookie:
        return cookie.strip()
    return None


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
        "type": "access",
    })

    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Декодировать и валидировать JWT токен."""
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
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


def _payload_as_user(payload: dict) -> dict:
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный тип токена",
            headers={"WWW-Authenticate": "Bearer"},
        )
    role = payload.get("role")
    if role not in ("viewer", "editor"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверная роль пользователя",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


# ── Dependencies ───────────────────────────────────────────────────────────

async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_optional),
) -> dict:
    """Текущий пользователь из Bearer или httpOnly cookie."""
    token = extract_access_token(request, credentials)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _payload_as_user(decode_access_token(token))


async def require_jwt_token(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Требовать валидный JWT (минимальная роль: viewer)."""
    return current_user


async def require_admin(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Админ: глобальная editor-ссылка (без привязки к человеку и без scope).

    Персональные ссылки (даже editor с полными правами) админом не являются:
    создавать и отзывать ссылки может только владелец глобальной ссылки.
    """
    if current_user.get("role") != "editor" or current_user.get("person_id") is not None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Создавать ссылки может только администратор",
        )
    return current_user


async def require_editor_token(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Требовать роль editor."""
    if current_user.get("role") != "editor":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Требуются права редактирования (editor role)",
        )
    return current_user


logger.info("Auth router initialized with JWT + httpOnly cookie support")


# ── JWT эндпоинты ─────────────────────────────────────────────────────────

@router.post("/token", response_model=TokenResponse, dependencies=[Depends(rate_limit_auth)])
async def login_with_token(
    request: Request,
    response: Response,
    request_data: TokenRequest,
) -> TokenResponse:
    """Обмен view/edit token на JWT. Токен также пишется в httpOnly cookie."""
    logger.info("Login attempt from %s", request.client.host if request.client else "unknown")

    await ensure_not_locked(request)

    clean_user_token = request_data.token.strip()

    role = None
    if tokens_equal(clean_user_token, settings.view_token):
        role = "viewer"
        logger.info("Login successful with view token")
    elif tokens_equal(clean_user_token, settings.edit_token):
        role = "editor"
        logger.info("Login successful with edit token")
    else:
        logger.warning("Invalid token for login (len=%d)", len(clean_user_token))
        await record_login_result(request, success=False)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный токен авторизации",
        )

    await record_login_result(request, success=True)

    access_token = create_access_token(data={"sub": "user", "role": role})
    expires_in = settings.jwt_access_token_expire_hours * 3600
    set_auth_cookie(response, request, access_token, expires_in)

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=expires_in,
        role=role,
    )


@router.get("/link/{access_token}", dependencies=[Depends(rate_limit_auth)])
async def login_by_link(
    request: Request,
    access_token: str,
) -> RedirectResponse:
    """
    Вход по ссылке-ключу: /auth/link/<view|edit token>.

    Деплой раздаёт два длинных адреса (read-only и edit) — токен входит
    в состав URL, вводить его не нужно. Валидный токен обменивается на
    JWT в httpOnly cookie и редиректит на главную. Сам токен в ссылках
    должен быть длинным случайным (см. scripts/generate_access_links.py).
    """
    await ensure_not_locked(request)

    clean = access_token.strip()

    # Персональная ссылка конкретного человека (viewer, фокус на человеке)
    from app.database import SessionLocal
    from app.models import PersonAccessLink
    from sqlalchemy import select as _select
    db = SessionLocal()
    try:
        link = db.scalars(
            _select(PersonAccessLink).where(
                PersonAccessLink.token == clean, PersonAccessLink.is_active
            )
        ).first()
        # считываем всё нужное до закрытия сессии (link.person — lazy load)
        if link is not None:
            _link_person_id = link.person_id
            _link_role = link.role
            _link_actor = (link.person.full_name or "")[:400] if link.person else None
            _link_scope = link.scope_depth
        else:
            _link_person_id = _link_role = _link_actor = _link_scope = None
    finally:
        db.close()
    if _link_person_id is not None:
        link = SimpleNamespace(
            person_id=_link_person_id, role=_link_role, person=None
        )
        actor_name = _link_actor
        scope_depth = _link_scope
    else:
        link = None
    if link is not None:
        await record_login_result(request, success=True)
        jwt_value = create_access_token(
            data={
                "sub": "user",
                "role": link.role if link.role in ("viewer", "editor") else "editor",
                "person_id": link.person_id,
                "actor": actor_name,
                "scope_depth": scope_depth,
            }
        )
        expires_in = settings.jwt_access_token_expire_hours * 3600
        base = settings.root_path or "/"
        redirect = RedirectResponse(
            url=f"{base}?person={link.person_id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
        set_auth_cookie(redirect, request, jwt_value, expires_in)
        return redirect

    if tokens_equal(clean, settings.view_token):
        role = "viewer"
        focus = settings.view_focus_person_id or None
        actor = None
        logger.info("Magic-link login successful (view token)")
    elif tokens_equal(clean, settings.edit_token):
        role = "editor"
        focus = None
        actor = settings.editor_name
        logger.info("Magic-link login successful (edit token)")
    else:
        logger.warning("Invalid magic-link token (len=%d)", len(clean))
        await record_login_result(request, success=False)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверная ссылка доступа",
        )

    await record_login_result(request, success=True)

    access_token_jwt = create_access_token(
        data={
            "sub": "user",
            "role": role,
            **({"person_id": focus} if focus else {}),
            **({"actor": actor} if actor else {}),
        }
    )
    expires_in = settings.jwt_access_token_expire_hours * 3600
    # при деплое за префиксом редиректим в корень приложения, не домена
    redirect_target = settings.root_path or "/"
    # nginx 301 при добавлении слэша теряет query — слэш добавляем сами
    if not redirect_target.endswith("/"):
        redirect_target += "/"
    if role == "viewer" and (settings.view_family or "").strip():
        # view-ссылка: отбор по фамилии (приоритетнее фокуса на человеке)
        from urllib.parse import quote

        redirect_target = f"{redirect_target}?family={quote(settings.view_family.strip())}"
    elif focus:
        redirect_target = f"{redirect_target}?person={focus}"
    redirect = RedirectResponse(url=redirect_target, status_code=status.HTTP_303_SEE_OTHER)
    set_auth_cookie(redirect, request, access_token_jwt, expires_in)
    return redirect


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: Request, response: Response) -> TokenResponse:
    """Обновить JWT (cookie или JSON {token} для API-клиентов)."""
    raw = extract_access_token(request)
    if not raw:
        try:
            body = await request.json()
            if isinstance(body, dict) and body.get("token"):
                raw = str(body["token"]).strip()
        except Exception:
            raw = None

    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Нет токена для обновления",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_access_token(raw)
        payload = _payload_as_user(payload)
        role = payload.get("role")
        logger.info("Token refresh for role: %s", role)

        access_token = create_access_token(data={"sub": "user", "role": role})
        expires_in = settings.jwt_access_token_expire_hours * 3600
        set_auth_cookie(response, request, access_token, expires_in)

        return TokenResponse(
            access_token=access_token,
            token_type="bearer",
            expires_in=expires_in,
            role=role,
        )
    except HTTPException:
        clear_auth_cookie(response, request)
        raise
    except Exception as e:
        logger.error("Error refreshing token: %s", e)
        clear_auth_cookie(response, request)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Не удалось обновить токен",
        )


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, bool]:
    """Сбросить httpOnly cookie сессии."""
    clear_auth_cookie(response, request)
    return {"ok": True}


@router.get("/me")
async def get_current_user_info(
    current_user: dict = Depends(require_jwt_token),
) -> dict:
    """Информация о текущей сессии (для восстановления UI после reload)."""
    return {
        "user": "user",
        "role": current_user.get("role"),
        "permissions": "read_write" if current_user.get("role") == "editor" else "read_only",
        "person_id": current_user.get("person_id"),
        "is_admin": current_user.get("role") == "editor"
        and current_user.get("person_id") is None,
        "exp": current_user.get("exp"),
        "iat": current_user.get("iat"),
    }
