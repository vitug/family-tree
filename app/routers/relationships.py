from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
import logging

from app.routers.auth import require_jwt_token
from app.database import get_db
from app.schemas import RelationshipCreate, RelationshipRead
from app.services.tree_service import TreeService

router = APIRouter(prefix="/relationships", tags=["relationships"])
logger = logging.getLogger(__name__)


def get_service(db: Session = Depends(get_db)) -> TreeService:
    return TreeService(db)


@router.get("", response_model=list[RelationshipRead])
def list_relationships(
    person_id: int | None = Query(None, description="Фильтр по участнику связи"),
    service: TreeService = Depends(get_service),
) -> list[RelationshipRead]:
    logger.debug("list_relationships: person_id=%s", person_id)
    return service.list_relationships(person_id=person_id)


@router.post("", response_model=RelationshipRead, status_code=201, dependencies=[Depends(require_jwt_token)])
def create_relationship(
    data: RelationshipCreate,
    service: TreeService = Depends(get_service),
) -> RelationshipRead:
    """Создать родственную связь.

    - `parent_of`: person_id — родитель, related_person_id — ребёнок
    - `spouse_of`: супруги (порядок id не важен)
    """
    logger.info("Create relationship: %s %s %s", data.relation_type, data.person_id, data.related_person_id)
    return service.add_relationship(data)


@router.delete("/{relationship_id}", status_code=204, dependencies=[Depends(require_jwt_token)])
def delete_relationship(
    relationship_id: int,
    service: TreeService = Depends(get_service),
) -> None:
    logger.info("Delete relationship id=%d", relationship_id)
    service.delete_relationship(relationship_id)
