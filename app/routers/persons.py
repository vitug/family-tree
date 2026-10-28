from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
import logging

from sqlalchemy import select

from app.routers.auth import require_admin, require_editor_token, require_jwt_token
from app.database import get_db
from app.models import Person, PersonAccessLink
from app.schemas import (
    PersonAccessLinkCreate,
    PersonAccessLinkRead,
    PersonCreate,
    PersonRead,
    PersonRevisionRead,
    PersonUpdate,
)
from app.services.tree_service import TreeService

router = APIRouter(prefix="/persons", tags=["persons"])
logger = logging.getLogger(__name__)


def get_service(
    db: Session = Depends(get_db),
    user: dict = Depends(require_jwt_token),
) -> TreeService:
    """Сервис с именем автора правок из текущей сессии (если есть)."""
    return TreeService(db, actor=user.get("actor"))


def _enforce_scope(db: Session, user: dict, *person_ids: int) -> None:
    """Scoped-ссылка правит только свою семью (scope_depth поколений)."""
    from fastapi import HTTPException, status as st

    from app.services.access_scope import can_edit_person

    for pid in person_ids:
        if pid is not None and not can_edit_person(db, user, pid):
            raise HTTPException(
                st.HTTP_403_FORBIDDEN,
                "Права ограничены семьёй этого человека",
            )


@router.get("", response_model=list[PersonRead], dependencies=[Depends(require_jwt_token)])
def list_persons(
    q: str | None = Query(None, description="Поиск по имени/фамилии"),
    family: str | None = Query(None, description="Отбор по фамилии + близкие родственники"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=1000),
    service: TreeService = Depends(get_service),
) -> list[PersonRead]:
    logger.debug("list_persons: q=%s skip=%d limit=%d", q or "", skip, limit)
    return service.list_persons(q=q, family=family, skip=skip, limit=limit)


@router.get("/{person_id}", response_model=PersonRead, dependencies=[Depends(require_jwt_token)])
def get_person(person_id: int, service: TreeService = Depends(get_service)) -> PersonRead:
    logger.debug("get_person: id=%d", person_id)
    return service.get_person(person_id)


@router.post("", response_model=PersonRead, status_code=201, dependencies=[Depends(require_editor_token)])
def create_person(data: PersonCreate, service: TreeService = Depends(get_service)) -> PersonRead:
    logger.info("Create person: %s %s", data.first_name, data.last_name or "")
    person = service.create_person(data)
    logger.debug("Created person id=%d", person.id)
    return person


@router.patch("/{person_id}", response_model=PersonRead, dependencies=[Depends(require_editor_token)])
def update_person(
    person_id: int,
    data: PersonUpdate,
    service: TreeService = Depends(get_service),
    user: dict = Depends(require_jwt_token),
    db: Session = Depends(get_db),
) -> PersonRead:
    _enforce_scope(db, user, person_id)
    logger.info("Update person id=%d", person_id)
    return service.update_person(person_id, data)
@router.delete("/{person_id}", status_code=204, dependencies=[Depends(require_editor_token)])
def delete_person(
    person_id: int,
    service: TreeService = Depends(get_service),
    user: dict = Depends(require_jwt_token),
    db: Session = Depends(get_db),
) -> None:
    _enforce_scope(db, user, person_id)
    logger.info("Delete person id=%d", person_id)
    service.delete_person(person_id)


@router.get(
    "/{person_id}/history",
    response_model=list[PersonRevisionRead],
    dependencies=[Depends(require_jwt_token)],
)
def person_history(
    person_id: int,
    limit: int = Query(100, ge=1, le=500),
    service: TreeService = Depends(get_service),
) -> list[PersonRevisionRead]:
    """История правок человека (create/update/delete с diff полей)."""
    return service.get_person_history(person_id, limit=limit)


# ── Персональные ссылки доступа ────────────────────────────────────────────

def _link_url(token: str) -> str:
    from app.config import settings
    base = settings.root_path or ""
    return f"{base}/api/v1/auth/link/{token}"


@router.post(
    "/{person_id}/access-link",
    response_model=PersonAccessLinkRead,
    dependencies=[Depends(require_admin)],
)
def create_access_link(
    person_id: int,
    data: PersonAccessLinkCreate | None = None,
    db: Session = Depends(get_db),
):
    """Создать персональную ссылку доступа для человека.

    role: editor (право править) или viewer (только просмотр).
    scope_depth: ограничить редактирование семьёй человека — глубина
    в поколениях (вверх и вниз + супруги); null/не задано = без ограничений.
    """
    import secrets

    data = data or PersonAccessLinkCreate()
    person = db.get(Person, person_id)
    if person is None:
        from fastapi import HTTPException, status

        raise HTTPException(status.HTTP_404_NOT_FOUND, "Человек не найден")
    token = secrets.token_urlsafe(32)
    link = PersonAccessLink(
        token=token,
        person_id=person_id,
        role=data.role if data.role in ("viewer", "editor") else "editor",
        scope_depth=data.scope_depth,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return PersonAccessLinkRead(
        id=link.id,
        person_id=link.person_id,
        url=_link_url(link.token),
        is_active=link.is_active,
        role=link.role,
        scope_depth=link.scope_depth,
        created_at=link.created_at,
    )


@router.get(
    "/{person_id}/access-links",
    response_model=list[PersonAccessLinkRead],
    dependencies=[Depends(require_admin)],
)
def list_access_links(person_id: int, db: Session = Depends(get_db)):
    links = (
        db.scalars(
            select(PersonAccessLink)
            .where(PersonAccessLink.person_id == person_id, PersonAccessLink.is_active)
            .order_by(PersonAccessLink.id.desc())
        ).all()
    )
    return [
        PersonAccessLinkRead(
            id=l.id, person_id=l.person_id, url=_link_url(l.token),
            is_active=l.is_active, role=l.role, scope_depth=l.scope_depth,
            created_at=l.created_at,
        )
        for l in links
    ]


@router.delete(
    "/access-links/{link_id}",
    status_code=204,
    dependencies=[Depends(require_admin)],
)
def revoke_access_link(link_id: int, db: Session = Depends(get_db)):
    link = db.get(PersonAccessLink, link_id)
    if link is None or not link.is_active:
        from fastapi import HTTPException, status

        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ссылка не найдена")
    link.is_active = False
    db.commit()
