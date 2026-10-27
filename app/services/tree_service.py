from __future__ import annotations

import json
from collections import deque

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Person, PersonRevision, RelationType, Relationship
from app.schemas import (
    AncestorsResponse,
    DescendantsResponse,
    FamilyMember,
    PathResponse,
    PersonCreate,
    PersonUpdate,
    RelativesResponse,
    RelationshipCreate,
    TreeNode,
    TreeResponse,
)

# cyrillic_lower регистрируется один раз в app.database при connect


def _serialize(value):
    """Значение поля в JSON-снимок для ревизии (даты, enum → строка)."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):  # date / datetime
        return value.isoformat()
    return str(value)


class TreeService:
    """Сервис управления людьми, родственными связями и построением дерева."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── Person CRUD ──────────────────────────────────────────────────────

    def _record_revision(
        self,
        person: Person,
        action: str,
        changes: list[dict] | None = None,
    ) -> None:
        """Записать ревизию в person_revisions (в той же транзакции)."""
        self.db.add(
            PersonRevision(
                person_id=person.id,
                person_name=person.full_name[:400],
                action=action,
                changes=json.dumps(changes, ensure_ascii=False) if changes else None,
            )
        )

    def create_person(self, data: PersonCreate) -> Person:
        person = Person(**data.model_dump())
        self.db.add(person)
        self.db.flush()  # нужен id до записи ревизии
        self._record_revision(person, "create")
        self.db.commit()
        self._invalidate_graph_cache()
        self.db.refresh(person)
        return person

    def get_person(self, person_id: int) -> Person:
        person = self.db.get(Person, person_id)
        if not person:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Человек id={person_id} не найден")
        return person

    def list_persons(
        self,
        *,
        q: str | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[Person]:
        stmt = select(Person).order_by(Person.id)
        if q:
            pattern = f"%{q.strip().lower()}%"
            # Функция cyrillic_lower уже зарегистрирована на engine (см. database.py)
            stmt = stmt.where(
                or_(
                    func.cyrillic_lower(Person.first_name).like(pattern),
                    func.cyrillic_lower(Person.last_name).like(pattern),
                    func.cyrillic_lower(Person.middle_name).like(pattern),
                    func.cyrillic_lower(Person.maiden_name).like(pattern),
                )
            )
        return list(self.db.scalars(stmt.offset(skip).limit(limit)).all())

    def update_person(self, person_id: int, data: PersonUpdate) -> Person:
        person = self.get_person(person_id)
        changes: list[dict] = []
        for field, value in data.model_dump(exclude_unset=True).items():
            old = getattr(person, field)
            if old != value:
                changes.append({"field": field, "old": _serialize(old), "new": _serialize(value)})
            setattr(person, field, value)
        if person.birth_date and person.death_date and person.death_date < person.birth_date:
            # явно откатываем незакоммиченные изменения до raise
            self.db.rollback()
            self.db.refresh(person)
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Дата смерти не может быть раньше даты рождения",
            )
        if changes:  # пустой PATCH (нет изменившихся полей) не шумит в истории
            self._record_revision(person, "update", changes)
        self.db.commit()
        self.db.refresh(person)
        return person

    def delete_person(self, person_id: int) -> None:
        from app.models import Media
        from app.security import unlink_media_file

        person = self.get_person(person_id)
        media_paths = list(
            self.db.scalars(select(Media.file_path).where(Media.person_id == person_id)).all()
        )
        if person.photo_path and person.photo_path not in media_paths:
            media_paths.append(person.photo_path)
        self.db.delete(person)
        # ревизия delete — до commit, в той же транзакции (переживёт удаление)
        self._record_revision(person, "delete")
        self.db.commit()
        self._invalidate_graph_cache()
        for path in media_paths:
            unlink_media_file(path)

    def get_person_history(self, person_id: int, limit: int = 100) -> list[PersonRevision]:
        """История правок человека (новые сверху). Человек может быть удалён."""
        return list(
            self.db.scalars(
                select(PersonRevision)
                .where(PersonRevision.person_id == person_id)
                .order_by(PersonRevision.id.desc())
                .limit(limit)
            ).all()
        )

    # ── Relationships ────────────────────────────────────────────────────

    def add_relationship(self, data: RelationshipCreate) -> Relationship:
        if data.person_id == data.related_person_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нельзя связать человека с самим собой")

        self.get_person(data.person_id)
        self.get_person(data.related_person_id)

        # Для супругов нормализуем направление: меньший id слева
        person_id, related_id = data.person_id, data.related_person_id
        if data.relation_type == RelationType.SPOUSE_OF and person_id > related_id:
            person_id, related_id = related_id, person_id

        existing = self.db.scalar(
            select(Relationship).where(
                Relationship.person_id == person_id,
                Relationship.related_person_id == related_id,
                Relationship.relation_type == data.relation_type,
            )
        )
        if existing:
            raise HTTPException(status.HTTP_409_CONFLICT, "Такая связь уже существует")

        # Обратная PARENT_OF недопустима (цикл parent↔child)
        if data.relation_type == RelationType.PARENT_OF:
            reverse = self.db.scalar(
                select(Relationship).where(
                    Relationship.person_id == related_id,
                    Relationship.related_person_id == person_id,
                    Relationship.relation_type == RelationType.PARENT_OF,
                )
            )
            if reverse:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Нельзя: обратная parent_of-связь уже есть (цикл родитель↔ребёнок)",
                )
            if self._would_create_ancestor_cycle(person_id, related_id):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Нельзя: связь создаёт цикл в генеалогии",
                )

        rel = Relationship(
            person_id=person_id,
            related_person_id=related_id,
            relation_type=data.relation_type,
            start_date=data.start_date,
            end_date=data.end_date,
            notes=data.notes,
        )
        self.db.add(rel)
        self.db.commit()
        self._invalidate_graph_cache()
        self.db.refresh(rel)
        return rel

    def list_relationships(self, person_id: int | None = None) -> list[Relationship]:
        stmt = select(Relationship).order_by(Relationship.id)
        if person_id is not None:
            self.get_person(person_id)
            stmt = stmt.where(
                or_(
                    Relationship.person_id == person_id,
                    Relationship.related_person_id == person_id,
                )
            )
        return list(self.db.scalars(stmt).all())

    def delete_relationship(self, relationship_id: int) -> None:
        rel = self.db.get(Relationship, relationship_id)
        if not rel:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Связь id={relationship_id} не найдена")
        self.db.delete(rel)
        self.db.commit()
        self._invalidate_graph_cache()

    # ── Graph helpers ────────────────────────────────────────────────────
    #
    # Раньше _parents_of/_children_of/_spouses_of делали SQL-запрос на каждого
    # человека (N+1: get_ancestors/get_descendants/build_tree — O(людей) запросов).
    # Теперь все PARENT_OF/SPOUSE_OF-связи грузятся одним запросом и строятся
    # индексы в памяти; кеш живёт в пределах инстанса сервиса (один HTTP-запрос).

    def _ensure_graph_cache(self) -> None:
        if getattr(self, "_graph_loaded", False):
            return
        parents: dict[int, list[int]] = {}
        children: dict[int, list[int]] = {}
        spouses: dict[int, set[int]] = {}
        for rel in self.db.scalars(
            select(Relationship).where(
                Relationship.relation_type.in_([RelationType.PARENT_OF, RelationType.SPOUSE_OF])
            )
        ).all():
            if rel.relation_type == RelationType.PARENT_OF:
                parents.setdefault(rel.related_person_id, []).append(rel.person_id)
                children.setdefault(rel.person_id, []).append(rel.related_person_id)
            else:
                spouses.setdefault(rel.person_id, set()).add(rel.related_person_id)
                spouses.setdefault(rel.related_person_id, set()).add(rel.person_id)
        self._parents_map = parents
        self._children_map = children
        self._spouse_map = spouses
        self._persons_by_id: dict[int, Person] = {
            p.id: p for p in self.db.scalars(select(Person)).all()
        }
        self._graph_loaded = True

    def _invalidate_graph_cache(self) -> None:
        self._graph_loaded = False

    def _lookup(self, ids: list[int] | set[int]) -> list[Person]:
        return [p for pid in ids if (p := self._persons_by_id.get(pid)) is not None]

    def _all_parent_links(self) -> list[Relationship]:
        return list(
            self.db.scalars(
                select(Relationship).where(Relationship.relation_type == RelationType.PARENT_OF)
            ).all()
        )

    def _parents_of(self, person_id: int) -> list[Person]:
        self._ensure_graph_cache()
        return self._lookup(self._parents_map.get(person_id, ()))

    def _children_of(self, person_id: int) -> list[Person]:
        self._ensure_graph_cache()
        return self._lookup(self._children_map.get(person_id, ()))

    def _spouses_of(self, person_id: int) -> list[Person]:
        self._ensure_graph_cache()
        return self._lookup(self._spouse_map.get(person_id, ()))

    def _siblings_of(self, person_id: int) -> list[Person]:
        self._ensure_graph_cache()
        sibling_ids: set[int] = set()
        for pid in self._parents_map.get(person_id, ()):
            sibling_ids.update(self._children_map.get(pid, ()))
        sibling_ids.discard(person_id)
        return self._lookup(sibling_ids)

    def _would_create_ancestor_cycle(self, parent_id: int, child_id: int) -> bool:
        """True, если parent_id уже является потомком child_id."""
        visited: set[int] = set()
        queue: deque[int] = deque([child_id])
        while queue:
            current = queue.popleft()
            if current == parent_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            for child in self._children_of(current):
                queue.append(child.id)
        return False

    @staticmethod
    def _to_member(
        person: Person,
        *,
        relation: str | None = None,
        generation: int = 0,
    ) -> FamilyMember:
        return FamilyMember(
            id=person.id,
            full_name=person.full_name,
            first_name=person.first_name,
            last_name=person.last_name,
            gender=person.gender,
            birth_date=person.birth_date,
            death_date=person.death_date,
            photo_url=person.photo_url,
            relation_to_root=relation,
            generation=generation,
        )

    # ── Public queries ───────────────────────────────────────────────────

    def get_relatives(self, person_id: int) -> RelativesResponse:
        self.get_person(person_id)
        return RelativesResponse(
            person_id=person_id,
            parents=[self._to_member(p, relation="parent") for p in self._parents_of(person_id)],
            children=[self._to_member(c, relation="child") for c in self._children_of(person_id)],
            spouses=[self._to_member(s, relation="spouse") for s in self._spouses_of(person_id)],
            siblings=[self._to_member(s, relation="sibling") for s in self._siblings_of(person_id)],
        )

    def get_ancestors(self, person_id: int, max_depth: int | None = None) -> AncestorsResponse:
        self.get_person(person_id)
        depth = max_depth or settings.max_tree_depth
        generations: list[list[FamilyMember]] = []
        current_ids = [person_id]
        visited: set[int] = {person_id}

        for gen in range(1, depth + 1):
            next_ids: list[int] = []
            members: list[FamilyMember] = []
            for cid in current_ids:
                for parent in self._parents_of(cid):
                    if parent.id in visited:
                        continue
                    visited.add(parent.id)
                    members.append(self._to_member(parent, relation="ancestor", generation=gen))
                    next_ids.append(parent.id)
            if not members:
                break
            generations.append(members)
            current_ids = next_ids

        return AncestorsResponse(person_id=person_id, generations=generations)

    def get_descendants(self, person_id: int, max_depth: int | None = None) -> DescendantsResponse:
        self.get_person(person_id)
        depth = max_depth or settings.max_tree_depth
        generations: list[list[FamilyMember]] = []
        current_ids = [person_id]
        visited: set[int] = {person_id}

        for gen in range(1, depth + 1):
            next_ids: list[int] = []
            members: list[FamilyMember] = []
            for cid in current_ids:
                for child in self._children_of(cid):
                    if child.id in visited:
                        continue
                    visited.add(child.id)
                    members.append(self._to_member(child, relation="descendant", generation=gen))
                    next_ids.append(child.id)
            if not members:
                break
            generations.append(members)
            current_ids = next_ids

        return DescendantsResponse(person_id=person_id, generations=generations)

    def build_tree(
        self,
        root_id: int,
        *,
        depth_up: int = 2,
        depth_down: int = 2,
    ) -> TreeResponse:
        root = self.get_person(root_id)
        depth_up = min(max(depth_up, 0), settings.max_tree_depth)
        depth_down = min(max(depth_down, 0), settings.max_tree_depth)
        counted: set[int] = set()

        def build_up(person: Person, remaining: int, gen: int) -> TreeNode:
            counted.add(person.id)
            node = TreeNode(
                person=self._to_member(person, generation=gen),
                spouses=[self._to_member(s, relation="spouse") for s in self._spouses_of(person.id)],
            )
            if remaining > 0:
                for parent in self._parents_of(person.id):
                    node.parents.append(build_up(parent, remaining - 1, gen - 1))
            return node

        def build_down(node: TreeNode, remaining: int, gen: int) -> None:
            if remaining <= 0:
                return
            for child in self._children_of(node.person.id):
                counted.add(child.id)
                child_node = TreeNode(
                    person=self._to_member(child, relation="child", generation=gen),
                    spouses=[
                        self._to_member(s, relation="spouse") for s in self._spouses_of(child.id)
                    ],
                )
                build_down(child_node, remaining - 1, gen + 1)
                node.children.append(child_node)

        tree = build_up(root, depth_up, 0)
        tree.person.relation_to_root = "root"
        build_down(tree, depth_down, 1)

        return TreeResponse(
            root_id=root_id,
            depth_up=depth_up,
            depth_down=depth_down,
            nodes_count=len(counted),
            tree=tree,
        )

    def find_path(self, from_id: int, to_id: int) -> PathResponse:
        """Кратчайший путь по графу (родители, дети, супруги)."""
        self.get_person(from_id)
        self.get_person(to_id)

        if from_id == to_id:
            p = self.get_person(from_id)
            return PathResponse(
                from_id=from_id,
                to_id=to_id,
                path=[self._to_member(p)],
                steps=[],
            )

        # BFS по undirected графу
        neighbors: dict[int, list[tuple[int, str]]] = {}

        def add_edge(a: int, b: int, label: str) -> None:
            neighbors.setdefault(a, []).append((b, label))
            neighbors.setdefault(b, []).append((a, self._reverse_label(label)))

        for rel in self.db.scalars(select(Relationship)).all():
            if rel.relation_type == RelationType.PARENT_OF:
                add_edge(rel.person_id, rel.related_person_id, "parent_of")
            elif rel.relation_type == RelationType.SPOUSE_OF:
                add_edge(rel.person_id, rel.related_person_id, "spouse_of")

        prev: dict[int, tuple[int, str] | None] = {from_id: None}
        queue: deque[int] = deque([from_id])
        found = False

        while queue:
            current = queue.popleft()
            if current == to_id:
                found = True
                break
            for nxt, label in neighbors.get(current, []):
                if nxt not in prev:
                    prev[nxt] = (current, label)
                    queue.append(nxt)

        if not found:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"Путь между id={from_id} и id={to_id} не найден",
            )

        # Восстановление пути
        chain: list[tuple[int, str | None]] = []
        cur: int | None = to_id
        while cur is not None:
            entry = prev[cur]
            if entry is None:
                chain.append((cur, None))
                break
            parent, label = entry
            chain.append((cur, label))
            cur = parent
        chain.reverse()

        path_persons = [self._to_member(self.get_person(pid)) for pid, _ in chain]
        steps = []
        for i in range(1, len(chain)):
            pid, label = chain[i]
            steps.append(f"{path_persons[i - 1].full_name} —[{label}]→ {path_persons[i].full_name}")

        return PathResponse(from_id=from_id, to_id=to_id, path=path_persons, steps=steps)

    @staticmethod
    def _reverse_label(label: str) -> str:
        return {
            "parent_of": "child_of",
            "child_of": "parent_of",
            "spouse_of": "spouse_of",
        }.get(label, label)
