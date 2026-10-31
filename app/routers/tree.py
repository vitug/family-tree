from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.routers.auth import require_jwt_token
from app.schemas import (
    AncestorsResponse,
    DescendantsResponse,
    PathResponse,
    RelativesResponse,
    TreeResponse,
)
from app.services.tree_service import TreeService
from app.services.privacy import (
    mask_member_list,
    mask_tree_node,
    viewer_allowed_ids as _viewer_allowed_ids,
)

router = APIRouter(prefix="/tree", tags=["tree"])


def get_service(db: Session = Depends(get_db)) -> TreeService:
    return TreeService(db)


@router.get("/{person_id}", response_model=TreeResponse, dependencies=[Depends(require_jwt_token)])
def get_tree(
    person_id: int,
    depth_up: int = Query(2, ge=0, le=20, description="Поколений вверх (предки)"),
    depth_down: int = Query(2, ge=0, le=20, description="Поколений вниз (потомки)"),
    service: TreeService = Depends(get_service),
    user: dict = Depends(require_jwt_token),
    db: Session = Depends(get_db),
) -> TreeResponse:
    """Построить семейное дерево вокруг человека."""
    resp = service.build_tree(person_id, depth_up=depth_up, depth_down=depth_down)
    allowed = _viewer_allowed_ids(db, user)
    if user.get("role") == "viewer":
        mask_tree_node(resp.tree, allowed)
    return resp


@router.get("/{person_id}/relatives", response_model=RelativesResponse, dependencies=[Depends(require_jwt_token)])
def get_relatives(
    person_id: int,
    service: TreeService = Depends(get_service),
    user: dict = Depends(require_jwt_token),
    db: Session = Depends(get_db),
) -> RelativesResponse:
    """Ближайшие родственники: родители, дети, супруги, братья/сёстры."""
    resp = service.get_relatives(person_id)
    allowed = _viewer_allowed_ids(db, user)
    if user.get("role") == "viewer":
        for key in ("parents", "children", "spouses", "siblings"):
            mask_member_list(getattr(resp, key, None), allowed)
    return resp


def _mask_generations(resp, user, allowed) -> None:
    """Для viewer скрыть в поколениях людей вне разрешённого круга."""
    if user.get("role") == "viewer":
        for gen in getattr(resp, "generations", None) or []:
            mask_member_list(getattr(gen, "members", None), allowed)


@router.get("/{person_id}/ancestors", response_model=AncestorsResponse, dependencies=[Depends(require_jwt_token)])
def get_ancestors(
    person_id: int,
    max_depth: int | None = Query(None, ge=1, le=20),
    service: TreeService = Depends(get_service),
    user: dict = Depends(require_jwt_token),
    db: Session = Depends(get_db),
) -> AncestorsResponse:
    """Предки по поколениям (1 — родители, 2 — бабушки/дедушки, …)."""
    resp = service.get_ancestors(person_id, max_depth=max_depth)
    _mask_generations(resp, user, _viewer_allowed_ids(db, user))
    return resp


@router.get("/{person_id}/descendants", response_model=DescendantsResponse, dependencies=[Depends(require_jwt_token)])
def get_descendants(
    person_id: int,
    max_depth: int | None = Query(None, ge=1, le=20),
    service: TreeService = Depends(get_service),
    user: dict = Depends(require_jwt_token),
    db: Session = Depends(get_db),
) -> DescendantsResponse:
    """Потомки по поколениям (1 — дети, 2 — внуки, …)."""
    resp = service.get_descendants(person_id, max_depth=max_depth)
    _mask_generations(resp, user, _viewer_allowed_ids(db, user))
    return resp


@router.get("/path/{from_id}/{to_id}", response_model=PathResponse, dependencies=[Depends(require_jwt_token)])
def find_path(
    from_id: int,
    to_id: int,
    service: TreeService = Depends(get_service),
) -> PathResponse:
    """Кратчайший путь родства между двумя людьми."""
    return service.find_path(from_id, to_id)
