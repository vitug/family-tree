from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth import require_edit_token
from app.database import get_db
from app.schemas import PersonCreate, PersonRead, PersonUpdate
from app.services.tree_service import TreeService

router = APIRouter(prefix="/persons", tags=["persons"])


def get_service(db: Session = Depends(get_db)) -> TreeService:
    return TreeService(db)


@router.get("", response_model=list[PersonRead])
def list_persons(
    q: str | None = Query(None, description="Поиск по имени/фамилии"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    service: TreeService = Depends(get_service),
) -> list[PersonRead]:
    return service.list_persons(q=q, skip=skip, limit=limit)


@router.get("/{person_id}", response_model=PersonRead)
def get_person(person_id: int, service: TreeService = Depends(get_service)) -> PersonRead:
    return service.get_person(person_id)


@router.post("", response_model=PersonRead, status_code=201, dependencies=[Depends(require_edit_token)])
def create_person(data: PersonCreate, service: TreeService = Depends(get_service)) -> PersonRead:
    return service.create_person(data)


@router.patch("/{person_id}", response_model=PersonRead, dependencies=[Depends(require_edit_token)])
def update_person(
    person_id: int,
    data: PersonUpdate,
    service: TreeService = Depends(get_service),
) -> PersonRead:
    return service.update_person(person_id, data)


@router.delete("/{person_id}", status_code=204, dependencies=[Depends(require_edit_token)])
def delete_person(person_id: int, service: TreeService = Depends(get_service)) -> None:
    service.delete_person(person_id)
