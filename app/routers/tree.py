from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    AncestorsResponse,
    DescendantsResponse,
    PathResponse,
    RelativesResponse,
    TreeResponse,
)
from app.services.tree_service import TreeService

router = APIRouter(prefix="/tree", tags=["tree"])


def get_service(db: Session = Depends(get_db)) -> TreeService:
    return TreeService(db)


@router.get("/{person_id}", response_model=TreeResponse)
def get_tree(
    person_id: int,
    depth_up: int = Query(2, ge=0, le=20, description="Поколений вверх (предки)"),
    depth_down: int = Query(2, ge=0, le=20, description="Поколений вниз (потомки)"),
    service: TreeService = Depends(get_service),
) -> TreeResponse:
    """Построить семейное дерево вокруг человека."""
    return service.build_tree(person_id, depth_up=depth_up, depth_down=depth_down)


@router.get("/{person_id}/relatives", response_model=RelativesResponse)
def get_relatives(
    person_id: int,
    service: TreeService = Depends(get_service),
) -> RelativesResponse:
    """Ближайшие родственники: родители, дети, супруги, братья/сёстры."""
    return service.get_relatives(person_id)


@router.get("/{person_id}/ancestors", response_model=AncestorsResponse)
def get_ancestors(
    person_id: int,
    max_depth: int | None = Query(None, ge=1, le=20),
    service: TreeService = Depends(get_service),
) -> AncestorsResponse:
    """Предки по поколениям (1 — родители, 2 — бабушки/дедушки, …)."""
    return service.get_ancestors(person_id, max_depth=max_depth)


@router.get("/{person_id}/descendants", response_model=DescendantsResponse)
def get_descendants(
    person_id: int,
    max_depth: int | None = Query(None, ge=1, le=20),
    service: TreeService = Depends(get_service),
) -> DescendantsResponse:
    """Потомки по поколениям (1 — дети, 2 — внуки, …)."""
    return service.get_descendants(person_id, max_depth=max_depth)


@router.get("/path/{from_id}/{to_id}", response_model=PathResponse)
def find_path(
    from_id: int,
    to_id: int,
    service: TreeService = Depends(get_service),
) -> PathResponse:
    """Кратчайший путь родства между двумя людьми."""
    return service.find_path(from_id, to_id)
