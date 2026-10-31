"""Валидатор генеалогического дерева.

Используется:
- при добавлении/изменении данных через API (блокирующие ошибки → 400);
- для полной проверки базы (scripts/validate_tree.py, после seed/импорта).

Правила:
- ОШИБКИ (блокируют): смерть раньше рождения; >2 родителей; возраст
  родителя при рождении ребёнка вне 13–70; родитель умер до рождения
  ребёнка; циклы родства; связь человека с самим собой; дубли связей.
- ПРЕДУПРЕЖДЕНИЯ (не блокируют): фамилия ребёнка при рождении не
  совпадает с родителями (двойные фамилии через дефис учитываются по
  любой части — фамилия жены не обязана совпадать с фамилией мужа);
  большая разница возрастов супругов; год рождения даёт >110 лет.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Person, RelationType, Relationship

MIN_PARENT_AGE = 13
MAX_PARENT_AGE = 70
MAX_LIFESPAN_WARN = 110
SPOUSE_AGE_GAP_WARN = 35


def validate_person_dates(birth: date | None, death: date | None) -> None:
    """Блокирующая проверка дат одного человека. Raise ValueError."""
    if birth and death and death < birth:
        raise ValueError("Дата смерти не может быть раньше даты рождения")


def _surname_parts(name: str | None) -> list[str]:
    """Части фамилии: двойная «Иванова-Петрова» → ["иванова", "петрова"]."""
    if not name:
        return []
    return [p.strip().lower() for p in name.replace("–", "-").split("-") if p.strip()]


def surname_matches(child_surname: str | None, parent: Person) -> bool:
    """Фамилия ребёнка (при рождении) vs любой из фамилий родителя
    (текущая, девичья), по любой части двойной фамилии. Без окончаний."""
    def stem(s: str) -> str:
        for suf in ("ова", "ева", "ина", "ая", "яя"):
            if s.endswith(suf):
                return s[: max(3, len(s) - 3)]
        for suf in ("ов", "ев", "ин", "ый", "ий", "ой"):
            if s.endswith(suf):
                return s[: max(3, len(s) - 2)]
        return s

    child_stems = {stem(x) for x in _surname_parts(child_surname)}
    parent_names = [parent.last_name, parent.maiden_name]
    parent_stems: set[str] = set()
    for n in parent_names:
        for x in _surname_parts(n):
            if len(x) >= 4:
                parent_stems.add(stem(x))
    return bool(child_stems & parent_stems) or not child_stems


def check_new_relationship(db: Session, data) -> list[str]:
    """Блокирующие проверки новой связи. Возвращает список ошибок
    (пустой = можно создавать). Вызывается до commit из add_relationship."""
    errors: list[str] = []
    a, b = data.person_id, data.related_person_id
    if a == b:
        return ["Нельзя связать человека с самим собой"]

    pa = db.get(Person, a)
    pb = db.get(Person, b)
    if pa is None or pb is None:
        return [f"Человек id={a if pa is None else b} не найден"]

    if data.relation_type == RelationType.PARENT_OF:
        parent, child = pa, pb
        # уже есть 2 родителя?
        existing = db.scalars(
            select(Relationship).where(
                Relationship.related_person_id == child.id,
                Relationship.relation_type == RelationType.PARENT_OF,
            )
        ).all()
        if len(existing) >= 2:
            errors.append(f"У {child.full_name} уже {len(existing)} родителей")
        if parent.birth_date and child.birth_date:
            years = (child.birth_date - parent.birth_date).days / 365.25
            if years < MIN_PARENT_AGE:
                errors.append(
                    f"{parent.full_name}: слишком молод для родителя "
                    f"({years:.0f} лет при рождении {child.full_name})"
                )
            if years > MAX_PARENT_AGE:
                errors.append(
                    f"{parent.full_name}: слишком стар для родителя "
                    f"({years:.0f} лет при рождении {child.full_name})"
                )
        if parent.death_date and child.birth_date and parent.death_date < child.birth_date:
            errors.append(f"{parent.full_name} умер(ла) до рождения {child.full_name}")

    return errors


def validate_tree(db: Session) -> dict:
    """Полный аудит базы: {stats, errors, warnings}. Для скриптов и тестов."""
    people = db.scalars(select(Person)).all()
    rels = db.scalars(select(Relationship)).all()
    byid = {p.id: p for p in people}
    errors: list[str] = []
    warnings: list[str] = []

    today = date.today()
    for p in people:
        try:
            validate_person_dates(p.birth_date, p.death_date)
        except ValueError as e:
            errors.append(f"id{p.id} {p.full_name}: {e}")
        if p.birth_date:
            end = p.death_date or today
            years = (end - p.birth_date).days / 365.25
            if years > MAX_LIFESPAN_WARN:
                warnings.append(
                    f"id{p.id} {p.full_name}: возраст {years:.0f} лет "
                    f"(дата смерти не указана?)"
                )

    par_of: dict[int, list[int]] = {}
    spouses: dict[int, set[int]] = {}
    seen: set = set()
    for r in rels:
        a, b = r.person_id, r.related_person_id
        if a == b:
            errors.append(f"id{a}: связь человека с самим собой")
            continue
        if r.relation_type == RelationType.PARENT_OF:
            if (a, b) in seen:
                errors.append(f"дубль связи PARENT_OF {a}->{b}")
            seen.add((a, b))
            par_of.setdefault(b, []).append(a)
        elif r.relation_type == RelationType.SPOUSE_OF:
            pair = tuple(sorted((a, b)))
            if pair in seen:
                errors.append(f"дубль связи SPOUSE_OF {pair}")
            seen.add(pair)
            spouses.setdefault(a, set()).add(b)
            spouses.setdefault(b, set()).add(a)

    for pid, parents in par_of.items():
        child = byid[pid]
        if len(parents) > 2:
            errors.append(f"id{pid} {child.full_name}: {len(parents)} родителей")
        for par in parents:
            pp = byid[par]
            try:
                for e in check_new_relationship(db, _rel(par, pid)):
                    if "уже" not in e:
                        errors.append(f"id{pid} {child.full_name}: {e}")
            except Exception:
                pass
            if not surname_matches(child.maiden_name or child.last_name, pp):
                warnings.append(
                    f"id{pid} {child.full_name}: фамилия при рождении "
                    f"«{child.maiden_name or child.last_name}» не совпадает "
                    f"с родителем {pp.full_name}"
                )

    def ancestors(pid: int, acc: set[int] | None = None) -> set[int]:
        acc = acc or set()
        for par in par_of.get(pid, []):
            if par in acc:
                continue
            acc.add(par)
            ancestors(par, acc)
        return acc

    for pid in par_of:
        if pid in ancestors(pid):
            errors.append(f"цикл родства у id{pid}")

    for a, lst in spouses.items():
        for b in lst:
            if a >= b:
                continue
            pa, pb = byid[a], byid[b]
            if pa.birth_date and pb.birth_date:
                gap = abs((pa.birth_date - pb.birth_date).days) / 365.25
                if gap > SPOUSE_AGE_GAP_WARN:
                    warnings.append(
                        f"разница возрастов супругов {pa.full_name} и "
                        f"{pb.full_name}: {gap:.0f} лет"
                    )

    linked = set(par_of) | {p for ps in par_of.values() for p in ps} | set(spouses)
    isolates = [byid[i].full_name for i in byid if i not in linked]
    if isolates:
        for name in isolates:
            warnings.append(f"без связей (изоляты): {name}")

    return {
        "stats": {
            "people": len(people),
            "relationships": len(rels),
            "parents": len(par_of),
            "spouse_pairs": sum(1 for a, lst in spouses.items() for b in lst if a < b),
            "with_photo": sum(1 for p in people if p.photo_path or p.photo_url),
        },
        "errors": errors,
        "warnings": warnings,
    }


class _rel:  # минимальный заглушечный объект для check_new_relationship
    def __init__(self, person_id: int, related_person_id: int):
        self.person_id = person_id
        self.related_person_id = related_person_id
        self.relation_type = RelationType.PARENT_OF
