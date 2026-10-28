from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
import logging

from app.routers.auth import require_jwt_token, require_editor_token
from app.database import get_db
from app.schemas import RelationshipCreate, RelationshipRead
from app.services.tree_service import TreeService

router = APIRouter(prefix="/relationships", tags=["relationships"])
logger = logging.getLogger(__name__)


def get_service(db: Session = Depends(get_db)) -> TreeService:
    return TreeService(db)


def _enforce_scope(db: Session, user: dict, *person_ids: int) -> None:
    """Scoped-ссылка: связывать можно только внутри своей семьи."""
    from fastapi import HTTPException, status as st

    from app.services.access_scope import can_edit_person

    allowed = [pid for pid in person_ids if pid is not None]
    if not allowed:
        return
    from app.services.access_scope import user_scope

    scope = user_scope(db, user)
    if scope is None:
        return
    if not any(pid in scope for pid in allowed):
        raise HTTPException(
            st.HTTP_403_FORBIDDEN,
            "Права ограничены семьёй этого человека",
        )


@router.get("", response_model=list[RelationshipRead], dependencies=[Depends(require_jwt_token)])
def list_relationships(
    person_id: int | None = Query(None, description="Фильтр по участнику связи"),
    service: TreeService = Depends(get_service),
) -> list[RelationshipRead]:
    logger.debug("list_relationships: person_id=%s", person_id)
    return service.list_relationships(person_id=person_id)


@router.post("", response_model=RelationshipRead, status_code=201, dependencies=[Depends(require_editor_token)])
def create_relationship(
    data: RelationshipCreate,
    service: TreeService = Depends(get_service),
    user: dict = Depends(require_jwt_token),
    db: Session = Depends(get_db),
) -> RelationshipRead:
    """Создать родственную связь.

    - `parent_of`: person_id — родитель, related_person_id — ребёнок
    - `spouse_of`: супруги (порядок id не важен)
    """
    logger.info("Create relationship: %s %s %s", data.relation_type, data.person_id, data.related_person_id)
    _enforce_scope(db, user, data.person_id, data.related_person_id)
    return service.add_relationship(data)


@router.delete("/{relationship_id}", status_code=204, dependencies=[Depends(require_editor_token)])
def delete_relationship(
    relationship_id: int,
    service: TreeService = Depends(get_service),
    user: dict = Depends(require_jwt_token),
    db: Session = Depends(get_db),
) -> None:
    logger.info("Delete relationship id=%d", relationship_id)
    from app.models import Relationship

    rel = db.get(Relationship, relationship_id)
    if rel is not None:
        _enforce_scope(db, user, rel.person_id, rel.related_person_id)
    service.delete_relationship(relationship_id)
