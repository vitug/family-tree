from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import RelationshipCreate, RelationshipRead
from app.services.tree_service import TreeService

router = APIRouter(prefix="/relationships", tags=["relationships"])


def get_service(db: Session = Depends(get_db)) -> TreeService:
    return TreeService(db)


@router.post("", response_model=RelationshipRead, status_code=201)
def create_relationship(
    data: RelationshipCreate,
    service: TreeService = Depends(get_service),
) -> RelationshipRead:
    """Создать родственную связь.

    - `parent_of`: person_id — родитель, related_person_id — ребёнок
    - `spouse_of`: супруги (порядок id не важен)
    """
    return service.add_relationship(data)


@router.get("", response_model=list[RelationshipRead])
def list_relationships(
    person_id: int | None = Query(None, description="Фильтр по участнику связи"),
    service: TreeService = Depends(get_service),
) -> list[RelationshipRead]:
    return service.list_relationships(person_id=person_id)


@router.delete("/{relationship_id}", status_code=204)
def delete_relationship(
    relationship_id: int,
    service: TreeService = Depends(get_service),
) -> None:
    service.delete_relationship(relationship_id)
