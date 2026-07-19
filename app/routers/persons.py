from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
import logging

from app.routers.auth import require_jwt_token
from app.database import get_db
from app.schemas import PersonCreate, PersonRead, PersonUpdate
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


@router.post("", response_model=PersonRead, status_code=201, dependencies=[Depends(require_jwt_token)])
def create_person(data: PersonCreate, service: TreeService = Depends(get_service)) -> PersonRead:
    logger.info("Create person: %s %s", data.first_name, data.last_name or "")
    person = service.create_person(data)
    logger.debug("Created person id=%d", person.id)
    return person


@router.patch("/{person_id}", response_model=PersonRead, dependencies=[Depends(require_jwt_token)])
def update_person(
    person_id: int,
    data: PersonUpdate,
    service: TreeService = Depends(get_service),
) -> PersonRead:
    logger.info("Update person id=%d", person_id)
    return service.update_person(person_id, data)
@router.delete("/{person_id}", status_code=204, dependencies=[Depends(require_jwt_token)])
def delete_person(person_id: int, service: TreeService = Depends(get_service)) -> None:
    logger.info("Delete person id=%d", person_id)
    service.delete_person(person_id)
