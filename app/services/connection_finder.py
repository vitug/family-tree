"""Поиск связи между двумя людьми по косвенным данным.

Находит кратчайший путь по графу родства (родители/дети/супруги) и
вычисляет человеческое название степени родства: «двоюродная сестра»,
«прадедушка», «племянник», «муж тёти» и т.п.

Чистокровные цепочки (вверх k1, вниз k2 без поворотов и супругов)
называются термином; смешанные (через супругов / несколько поворотов)
описываются цепочкой шагов.
"""
from __future__ import annotations

from collections import deque

from sqlalchemy.orm import Session

from app.models import Person
from app.services.tree_service import TreeService


def _gender_word(p: Person, male: str, female: str) -> str:
    return male if p.gender == "male" else female


def _ancestor_term(up: int, target: Person) -> str:
    """k поколений вверх от исходного к target."""
    if up == 1:
        return _gender_word(target, "отец", "мать")
    if up == 2:
        return _gender_word(target, "дедушка", "бабушка")
    prefix = "пра" * (up - 2)
    return prefix + _gender_word(target, "дедушка", "бабушка")


def _descendant_term(down: int, target: Person) -> str:
    """k поколений вниз от исходного к target."""
    if down == 1:
        return _gender_word(target, "сын", "дочь")
    if down == 2:
        return _gender_word(target, "внук", "внучка")
    prefix = "прав" if down == 3 else "пра" * (down - 2)
    return prefix + _gender_word(target, "внук", "внучка")


def _collateral_term(up: int, down: int, target: Person) -> str:
    """Общий предок: up вверх, down вниз."""
    cousin = min(up, down)  # 1 = брат/сестра, 2 = двоюродный, 3 = троюродный
    if up == 1 and down == 1:
        return _gender_word(target, "брат", "сестра")
    if up == 1 and down == 2 or up == 2 and down == 1:
        # старшее поколение (up>down) — дядя/тётя; младшее — племянник
        if up > down:
            return _gender_word(target, "дядя", "тётя")
        return _gender_word(target, "племянник", "племянница")
    if up == 2 and down == 2:
        return _gender_word(target, "двоюродный брат", "двоюродная сестра")
    if up == 3 and down == 2:
        return _gender_word(target, "двоюродный дядя", "двоюродная тётя")
    if up == 2 and down == 3:
        return _gender_word(target, "двоюродный племянник", "двоюродная племянница")
    num = {1: "двоюродный", 2: "двоюродный", 3: "троюродный", 4: "четвероюродный"}.get(
        cousin, f"{cousin}юродный"
    )
    num_f = num.replace("юродный", "юродная")
    if up == down:
        return _gender_word(target, num + " брат", num_f + " сестра")
    return _gender_word(
        target,
        num + " дядя/племянник (через общего предка)",
        num_f + " тётя/племянница (через общего предка)",
    )


def find_connection(service: TreeService, person_id: int, target_id: int) -> dict:
    """Кратчайший путь person_id → target_id по рёбрам родства."""
    service._ensure_graph_cache()
    if person_id == target_id:
        raise ValueError("Это один и тот же человек")
    for pid in (person_id, target_id):
        if pid not in service._persons_by_id:
            raise LookupError(f"Человек id={pid} не найден")

    # BFS: для каждого достижимого id — (предыдущий id, тип ребра)
    prev: dict[int, tuple[int, str]] = {}
    queue: deque[int] = deque([person_id])
    while queue:
        cur = queue.popleft()
        if cur == target_id:
            break
        neighbours: list[tuple[int, str]] = []
        for pid in service._parents_map.get(cur, ()):
            neighbours.append((pid, "parent"))
        for pid in service._children_map.get(cur, ()):
            neighbours.append((pid, "child"))
        for pid in service._spouse_map.get(cur, ()):
            neighbours.append((pid, "spouse"))
        for nxt, edge in neighbours:
            if nxt not in prev and nxt not in queue and nxt != person_id:
                prev[nxt] = (cur, edge)
                queue.append(nxt)

    if target_id not in prev:
        return {"found": False, "person_id": person_id, "target_id": target_id}

    # восстановить путь
    chain: list[dict] = []
    cur = target_id
    while cur != person_id:
        p, edge = prev[cur]
        chain.append(
            {
                "id": cur,
                "name": service._persons_by_id[cur].full_name,
                "edge_from_prev": edge,
            }
        )
        cur = p
    chain.reverse()  # от соседа исходного к target

    edges = [c["edge_from_prev"] for c in chain]
    term = _describe_edges(service, person_id, target_id, edges)
    return {
        "found": True,
        "person_id": person_id,
        "target_id": target_id,
        "target_name": service._persons_by_id[target_id].full_name,
        "degree": len(chain),
        "term": term,
        "chain": chain,
    }


def _describe_edges(service: TreeService, src: int, dst: int, edges: list[str]) -> str:
    """Название степени по последовательности рёбер (parent/child/spouse)."""
    target = service._persons_by_id[dst]
    # чистокровная цепочка: вверх*, затем вниз*, без супругов
    if "spouse" not in edges:
        up = 0
        i = 0
        while i < len(edges) and edges[i] == "parent":
            up += 1
            i += 1
        down = len(edges) - i
        if all(e == "child" for e in edges[i:]):
            if up and not down:
                return _ancestor_term(up, target)
            if down and not up:
                return _descendant_term(down, target)
            if up and down:
                return _collateral_term(up, down, target)
    # через супругов / несколько поворотов — описательно
    labels = {
        "parent": "родитель",
        "child": "ребёнок",
        "spouse": "супруг(а)",
    }
    if edges == ["spouse"]:
        return _gender_word(target, "муж", "жена")
    parts = []
    for e in edges:
        if not parts or parts[-1] != labels[e]:
            parts.append(labels[e])
    return "связь через: " + " → ".join(parts)


def compute_connection(db: Session, person_id: int, target_id: int) -> dict:
    """Точка входа без создания сервиса вручную."""
    return find_connection(TreeService(db), person_id, target_id)
