"""Импорт GEDCOM 5.5.1 (.ged): люди, семьи, связи.

Xref-указатели (@I12@) сохраняются в Person.external_id (external_source=
"gedcom") — повторный импорт того же файла обновляет людей, а не создаёт
дубликаты (матчинг по external_source + external_id).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from app.models import Person, RelationType, Relationship

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTHS_RU = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "мая": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}


@dataclass
class GedcomStats:
    people: int = 0
    families: int = 0
    relationships: int = 0
    updated_people: int = 0
    skipped_people: int = 0
    warnings: list[str] = field(default_factory=list)


def _parse_gedcom_date(value: str) -> date | None:
    """«12 JAN 1948» / «JAN 1948» / «1948» / «ABT 1930» → date (без дня — 1-е число).

    Неточные даты (ABT/EST/BEF/AFT) приводим к году; при полном провале — None.
    """
    v = (value or "").strip()
    if not v:
        return None
    v = re.sub(r"^(ABT|EST|CAL|BEF|AFT|FROM|TO|BET)\s+", "", v, flags=re.IGNORECASE)
    m = re.search(r"(\d{1,2})?\s*([A-Za-zА-Яа-яЁё]{3,9})?\s*(\d{4})", v)
    if not m:
        m2 = re.search(r"(\d{4})", v)
        if m2:
            return date(int(m2.group(1)), 1, 1)
        return None
    day_s, mon_s, year_s = m.groups()
    year = int(year_s)
    month = 1
    if mon_s:
        key = mon_s.lower()[:3]
        month = _MONTHS.get(key) or _MONTHS_RU.get(key) or 1
    day = int(day_s) if day_s else 1
    try:
        return date(year, month, day)
    except ValueError:
        return date(year, month, 1)


def _parse_lines(text: str) -> list[tuple[int, str, str, str]]:
    """Строки GEDCOM → (level, xref, tag, value)."""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("@#"):  # пустые и charset-маркеры
            continue
        m = re.match(r"^(\d+)\s+(?:(@\S+@)\s+)?(\S+)(?:\s(.*))?$", line)
        if not m:
            continue
        out.append((int(m.group(1)), m.group(2) or "", m.group(3), (m.group(4) or "").strip()))
    return out


class _Node:
    __slots__ = ("tag", "value", "children")

    def __init__(self, tag: str, value: str = "") -> None:
        self.tag = tag
        self.value = value
        self.children: list[_Node] = []

    def child(self, tag: str) -> "_Node | None":
        for c in self.children:
            if c.tag == tag:
                return c
        return None

    def child_value(self, tag: str) -> str:
        c = self.child(tag)
        return c.value if c else ""


def _build_tree(lines: list[tuple[int, str, str, str]]) -> list[_Node]:
    """Линейный список → дерево верхнего уровня (записи level 0)."""
    roots: list[_Node] = []
    stack: list[tuple[int, _Node]] = []
    for level, xref, tag, value in lines:
        node = _Node(tag, value if not xref else value)
        if xref:
            # xref обычно у level-0 записей; для подчинённых кладём в value
            node.value = value
        if level == 0:
            if xref:
                node.value = xref  # «@I12@ INDI» → tag=INDI, value=xref
            roots.append(node)
            stack = [(0, node)]
            continue
        while stack and stack[-1][0] >= level:
            stack.pop()
        if stack:
            stack[-1][1].children.append(node)
            stack.append((level, node))
        else:
            roots.append(node)
            stack = [(0, node)]
    return roots


class GedcomImporter:
    """Парсит GEDCOM 5.5.1 и записывает людей и семьи."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def import_bytes(self, data: bytes, *, replace: bool = False) -> GedcomStats:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = data.decode("cp1251")
            except UnicodeDecodeError:
                text = data.decode("latin-1")
        if "\u0000" in text:
            # UTF-16
            text = data.decode("utf-16")
        return self.import_text(text, replace=replace)

    def import_text(self, text: str, *, replace: bool = False) -> GedcomStats:
        stats = GedcomStats()
        if replace:
            self._clear_all()

        lines = _parse_lines(text)
        if not lines:
            raise ValueError("Файл не похож на GEDCOM: нет распознанных строк")

        records = _build_tree(lines)
        indis = [r for r in records if r.tag == "INDI"]
        fams = [r for r in records if r.tag == "FAM"]
        if not indis and not fams:
            raise ValueError("В файле нет записей INDI/FAM — это GEDCOM?")

        xref_to_person: dict[str, Person] = {}
        for indi in indis:
            person = self._upsert_person(indi, stats)
            if person is not None:
                xref_to_person[indi.value] = person

        for fam in fams:
            self._import_family(fam, xref_to_person, stats)

        self.db.commit()
        if not stats.warnings and stats.people == 0 and stats.updated_people == 0:
            stats.warnings.append("Людей не импортировано")
        return stats

    # ── люди ───────────────────────────────────────────────────────────────

    def _upsert_person(self, indi: _Node, stats: GedcomStats) -> Person | None:
        xref = indi.value  # «@I12@»
        name_node = indi.child("NAME")
        raw_name = name_node.value if name_node else ""
        first_name, last_name, middle_name = _split_name(raw_name)
        if not first_name:
            first_name = xref or "Без имени"
            stats.warnings.append(f"У {xref} нет имени, подставлено «{first_name}»")

        sex_raw = indi.child_value("SEX").upper()
        gender = {"M": "male", "F": "female"}.get(sex_raw, "unknown")

        birth = indi.child("BIRT")
        death = indi.child("DEAT")
        birth_date = _parse_gedcom_date(birth.child_value("DATE")) if birth else None
        death_date = _parse_gedcom_date(death.child_value("DATE")) if death else None
        birth_place = (birth.child_value("PLAC") if birth else "")[:255] or None
        note = (indi.child_value("NOTE") or "")[:2000] or None

        existing = (
            self.db.query(Person)
            .filter_by(external_source="gedcom", external_id=xref[:64])
            .first()
        )
        if existing is not None:
            existing.first_name = first_name[:100]
            existing.last_name = last_name
            existing.middle_name = middle_name
            existing.gender = gender
            existing.birth_date = birth_date
            existing.death_date = death_date
            existing.birth_place = birth_place
            existing.notes = note
            stats.updated_people += 1
            return existing

        person = Person(
            first_name=first_name[:100],
            last_name=last_name,
            middle_name=middle_name,
            gender=gender,
            birth_date=birth_date,
            death_date=death_date,
            birth_place=birth_place,
            notes=note,
            external_id=xref[:64] if xref else None,
            external_source="gedcom" if xref else None,
        )
        self.db.add(person)
        self.db.flush()
        stats.people += 1
        return person

    # ── семьи ──────────────────────────────────────────────────────────────

    def _import_family(self, fam: _Node, xref_to_person: dict[str, Person], stats: GedcomStats) -> None:
        stats.families += 1
        husb = xref_to_person.get(fam.child_value("HUSB"))
        wife = xref_to_person.get(fam.child_value("WIFE"))
        children = [
            xref_to_person[ch.value]
            for ch in fam.children
            if ch.tag == "CHIL" and ch.value in xref_to_person
        ]

        if husb is not None and wife is not None:
            self._add_rel(husb.id, wife.id, RelationType.SPOUSE_OF, stats)
        for parent in (p for p in (husb, wife) if p is not None):
            for child in children:
                self._add_rel(parent.id, child.id, RelationType.PARENT_OF, stats)

    def _add_rel(self, person_id: int, related_id: int, rtype: RelationType, stats: GedcomStats) -> None:
        exists = (
            self.db.query(Relationship)
            .filter_by(
                person_id=person_id,
                related_person_id=related_id,
                relation_type=rtype,
            )
            .first()
        )
        if exists:
            return
        self.db.add(Relationship(person_id=person_id, related_person_id=related_id, relation_type=rtype))
        stats.relationships += 1

    def _clear_all(self) -> None:
        self.db.query(Relationship).delete()
        self.db.query(Person).delete()
        self.db.flush()


def _split_name(raw: str) -> tuple[str, str | None, str | None]:
    """«Иван Петрович /Петров/» → (Иван, Петров, Петрович)."""
    raw = (raw or "").strip()
    m = re.match(r"^(.*?)\s*/(.*)/\s*(.*)$", raw)
    given = m.group(1).strip() if m else raw
    surname = (m.group(2).strip() or None) if m else None
    tokens = given.split()
    first_name = tokens[0] if tokens else ""
    middle_name = " ".join(tokens[1:]) if len(tokens) > 1 else None
    return first_name, surname, middle_name
