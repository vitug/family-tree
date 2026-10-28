from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
import logging

from sqlalchemy import select

from app.routers.auth import require_jwt_token, require_editor_token
from app.database import get_db
from app.models import Person, PersonAccessLink
from app.schemas import (
    PersonAccessLinkRead,
    PersonCreate,
    PersonRead,
    PersonRevisionRead,
    PersonUpdate,
)
from app.services.tree_service import TreeService

router = APIRouter(prefix="/persons", tags=["persons"])
logger = logging.getLogger(__name__)


def get_service(db: Session = Depends(get_db)) -> TreeService:
    return TreeService(db)


@router.get("", response_model=list[PersonRead], dependencies=[Depends(require_jwt_token)])
def list_persons(
    q: str | None = Query(None, description="Поиск по имени/фамилии"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    service: TreeService = Depends(get_service),
) -> list[PersonRead]:
    logger.debug("list_persons: q=%s skip=%d limit=%d", q or "", skip, limit)
    return service.list_persons(q=q, skip=skip, limit=limit)


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
) -> PersonRead:
    logger.info("Update person id=%d", person_id)
    return service.update_person(person_id, data)
@router.delete("/{person_id}", status_code=204, dependencies=[Depends(require_editor_token)])
def delete_person(person_id: int, service: TreeService = Depends(get_service)) -> None:
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
    dependencies=[Depends(require_editor_token)],
)
def create_access_link(person_id: int, db: Session = Depends(get_db)):
    """Создать персональную ссылку доступа для человека (viewer, фокус на нём)."""
    import secrets

    person = db.get(Person, person_id)
    if person is None:
        from fastapi import HTTPException, status

        raise HTTPException(status.HTTP_404_NOT_FOUND, "Человек не найден")
    token = secrets.token_urlsafe(32)
    link = PersonAccessLink(token=token, person_id=person_id)
    db.add(link)
    db.commit()
    db.refresh(link)
    return PersonAccessLinkRead(
        id=link.id,
        person_id=link.person_id,
        url=_link_url(link.token),
        is_active=link.is_active,
        created_at=link.created_at,
    )


@router.get(
    "/{person_id}/access-links",
    response_model=list[PersonAccessLinkRead],
    dependencies=[Depends(require_editor_token)],
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
            is_active=l.is_active, created_at=l.created_at,
        )
        for l in links
    ]


@router.delete(
    "/access-links/{link_id}",
    status_code=204,
    dependencies=[Depends(require_editor_token)],
)
def revoke_access_link(link_id: int, db: Session = Depends(get_db)):
    link = db.get(PersonAccessLink, link_id)
    if link is None or not link.is_active:
        from fastapi import HTTPException, status

        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ссылка не найдена")
    link.is_active = False
    db.commit()
