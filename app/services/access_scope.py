"""Ограничение прав редактирования одной семьёй с глубиной в поколениях.

Scoped-ссылка (PersonAccessLink.scope_depth = N) разрешает правки только
людям из семьи владельца ссылки: N поколений вверх (родители и их супруги),
N поколений вниз (дети и их супруги), плюс супруги на каждом уровне.
scope_depth = NULL — без ограничений (всё дерево).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Relationship


def _spouses_of(db: Session, ids: set[int]) -> set[int]:
    if not ids:
        return set()
    rows = db.execute(
        select(Relationship.person_id, Relationship.related_person_id).where(
            Relationship.relation_type == "spouse_of",
            Relationship.person_id.in_(ids),
        )
    ).all()
    rows += db.execute(
        select(Relationship.person_id, Relationship.related_person_id).where(
            Relationship.relation_type == "spouse_of",
            Relationship.related_person_id.in_(ids),
        )
    ).all()
    return {b for a, b in rows if b not in ids}


def _linked_by_parent_of(
    db: Session, ids: set[int], *, column, match_column
) -> set[int]:
    """Общий запрос по parent_of: вернуть column там, где match_column in ids."""
    if not ids:
        return set()
    rows = db.execute(
        select(column).where(
            Relationship.relation_type == "parent_of",
            match_column.in_(ids),
        )
    ).all()
    return {r[0] for r in rows}


def _parents_of(db: Session, ids: set[int]) -> set[int]:
    """Родители людей из ids (person_id — родитель, related — ребёнок)."""
    return _linked_by_parent_of(
        db, ids,
        column=Relationship.person_id,
        match_column=Relationship.related_person_id,
    )


def _children_of(db: Session, ids: set[int]) -> set[int]:
    return _linked_by_parent_of(
        db, ids,
        column=Relationship.related_person_id,
        match_column=Relationship.person_id,
    )


def family_person_ids(db: Session, person_id: int, depth: int) -> set[int]:
    """id людей семьи person_id в пределах depth поколений вверх/вниз."""
    allowed = {person_id}
    level = {person_id}
    for _ in range(max(0, depth)):
        level |= _spouses_of(db, level)
        allowed |= level
        nxt: set[int] = set()
        parents = _parents_of(db, level)
        if parents:
            nxt |= parents | _spouses_of(db, parents)
        children = _children_of(db, level)
        if children:
            nxt |= children | _spouses_of(db, children)
        if not nxt:
            break
        allowed |= nxt
        level = nxt
    # супруги финального уровня тоже часть семьи
    allowed |= _spouses_of(db, level)
    return allowed


def user_scope(db: Session, user: dict) -> set[int] | None:
    """Множество разрешённых id для сессии; None = без ограничений."""
    depth = user.get("scope_depth")
    person_id = user.get("person_id")
    if depth is None or person_id is None:
        return None
    return family_person_ids(db, int(person_id), int(depth))


def can_edit_person(db: Session, user: dict, person_id: int) -> bool:
    scope = user_scope(db, user)
    return scope is None or person_id in scope
