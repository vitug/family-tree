"""Импорт GEDCOM 5.5.1 (.ged): люди, семьи, связи. Двухфазный:

1. parse() — разбор файла без записи в базу (preview + редактор связей)
2. apply(..., decisions) — запись с решениями пользователя:
   key → person_id (склеить с существующей записью) или None (новый)

Xref-указатели (@I12@) сохраняются в таблице person_external_refs
(source="gedcom"); у одного человека могут быть и Gramps ID, и GEDCOM xref.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from app.models import Person, PersonExternalRef, RelationType, Relationship
from app.services.import_matcher import ParsedPerson

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTHS_RU = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "мая": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}


@dataclass
class ParsedFamily:
    husb_key: str | None = None
    wife_key: str | None = None
    child_keys: list[str] = field(default_factory=list)


@dataclass
class GedcomParsed:
    people: list[ParsedPerson] = field(default_factory=list)
    families: list[ParsedFamily] = field(default_factory=list)


@dataclass
class GedcomStats:
    people: int = 0
    families: int = 0
    relationships: int = 0
    updated_people: int = 0
    linked_people: int = 0  # склеено с существующими по решению пользователя
    skipped_people: int = 0
    warnings: list[str] = field(default_factory=list)


def _parse_gedcom_date(value: str) -> date | None:
    """«12 JAN 1948» / «JAN 1948» / «1948» / «ABT 1930» → date (без дня — 1-е число)."""
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
        if not line or line.startswith("@#"):
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
        node = _Node(tag, value)
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


def _decode(data: bytes) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = data.decode("cp1251")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
    if "\u0000" in text:
        text = data.decode("utf-16")
    return text


class GedcomImporter:
    """Парсит GEDCOM 5.5.1 и записывает людей и семьи."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── фаза 1: разбор без записи ──────────────────────────────────────────

    def parse(self, text: str) -> GedcomParsed:
        lines = _parse_lines(text)
        if not lines:
            raise ValueError("Файл не похож на GEDCOM: нет распознанных строк")
        records = _build_tree(lines)
        parsed = GedcomParsed()
        for indi in records:
            if indi.tag != "INDI":
                continue
            first_name, last_name, middle_name = _split_name(indi.child_value("NAME"))
            birth = indi.child("BIRT")
            death = indi.child("DEAT")
            parsed.people.append(
                ParsedPerson(
                    key=indi.value,
                    first_name=(first_name or indi.value or "Без имени"),
                    last_name=last_name,
                    middle_name=middle_name,
                    gender={"M": "male", "F": "female"}.get(
                        indi.child_value("SEX").upper(), "unknown"
                    ),
                    birth_date=_parse_gedcom_date(birth.child_value("DATE")) if birth else None,
                    death_date=_parse_gedcom_date(death.child_value("DATE")) if death else None,
                    birth_place=(birth.child_value("PLAC") if birth else "")[:255] or None,
                    notes=(indi.child_value("NOTE") or "")[:2000] or None,
                )
            )
        for fam in records:
            if fam.tag != "FAM":
                continue
            parsed.families.append(
                ParsedFamily(
                    husb_key=fam.child_value("HUSB") or None,
                    wife_key=fam.child_value("WIFE") or None,
                    child_keys=[c.value for c in fam.children if c.tag == "CHIL"],
                )
            )
        if not parsed.people and not parsed.families:
            raise ValueError("В файле нет записей INDI/FAM — это GEDCOM?")
        return parsed

    def parse_bytes(self, data: bytes) -> GedcomParsed:
        return self.parse(_decode(data))

    # ── фаза 2: запись с решениями пользователя ────────────────────────────

    def apply(
        self,
        parsed: GedcomParsed,
        *,
        decisions: dict[str, int | None] | None = None,
        replace: bool = False,
    ) -> GedcomStats:
        stats = GedcomStats()
        decisions = decisions or {}
        if replace:
            self._clear_all()

        key_to_person: dict[str, Person] = {}
        for person in parsed.people:
            db_person = self._apply_person(person, decisions.get(person.key), stats)
            if db_person is not None:
                key_to_person[person.key] = db_person

        for fam in parsed.families:
            self._apply_family(fam, key_to_person, stats)

        self.db.commit()
        return stats

    def _apply_person(
        self, person: ParsedPerson, decision: int | None, stats: GedcomStats
    ) -> Person | None:
        existing = None
        force_new = decision == -1
        if decision is not None and decision > 0:
            existing = self.db.get(Person, decision)
        if existing is None and not force_new:
            existing = (
                self.db.query(Person)
                .join(PersonExternalRef)
                .filter(
                    PersonExternalRef.source == "gedcom",
                    PersonExternalRef.external_id == (person.key or "")[:64],
                )
                .first()
            )

        if existing is not None:
            if decision is not None:
                stats.linked_people += 1
            else:
                stats.updated_people += 1
            self._update_fields(existing, person)
            self._ensure_ref(existing, person.key)
            return existing

        new = Person(
            first_name=person.first_name[:100],
            last_name=person.last_name,
            middle_name=person.middle_name,
            maiden_name=person.maiden_name,
            gender=person.gender,
            birth_date=person.birth_date,
            death_date=person.death_date,
            birth_place=person.birth_place,
            notes=person.notes,
            external_id=(person.key[:64] if person.key else None),
            external_source=("gedcom" if person.key else None),
        )
        self.db.add(new)
        self.db.flush()
        self._ensure_ref(new, person.key)
        stats.people += 1
        return new

    def _update_fields(self, p: Person, person: ParsedPerson) -> None:
        p.first_name = person.first_name[:100]
        p.last_name = person.last_name
        p.middle_name = person.middle_name
        p.maiden_name = person.maiden_name
        p.gender = person.gender
        p.birth_date = person.birth_date
        p.death_date = person.death_date
        p.birth_place = person.birth_place
        p.notes = person.notes
        if person.key:
            p.external_id = person.key[:64]
            p.external_source = "gedcom"

    def _ensure_ref(self, person: Person, key: str) -> None:
        if not key:
            return
        exists = (
            self.db.query(PersonExternalRef)
            .filter_by(source="gedcom", external_id=key[:64])
            .first()
        )
        if exists is None:
            self.db.add(
                PersonExternalRef(person_id=person.id, source="gedcom", external_id=key[:64])
            )

    def _apply_family(
        self, fam: ParsedFamily, key_to_person: dict[str, Person], stats: GedcomStats
    ) -> None:
        stats.families += 1
        husb = key_to_person.get(fam.husb_key or "")
        wife = key_to_person.get(fam.wife_key or "")
        children = [key_to_person[k] for k in fam.child_keys if k in key_to_person]

        if husb is not None and wife is not None:
            self._add_rel(husb.id, wife.id, RelationType.SPOUSE_OF, stats)
        for parent in (p for p in (husb, wife) if p is not None):
            for child in children:
                self._add_rel(parent.id, child.id, RelationType.PARENT_OF, stats)

    def _add_rel(self, person_id: int, related_id: int, rtype: RelationType, stats: GedcomStats) -> None:
        exists = (
            self.db.query(Relationship)
            .filter_by(person_id=person_id, related_person_id=related_id, relation_type=rtype)
            .first()
        )
        if exists:
            return
        self.db.add(Relationship(person_id=person_id, related_person_id=related_id, relation_type=rtype))
        stats.relationships += 1

    def _clear_all(self) -> None:
        self.db.query(PersonExternalRef).delete()
        self.db.query(Relationship).delete()
        self.db.query(Person).delete()
        self.db.flush()

    # ── совместимость со старым однофазным API ─────────────────────────────

    def import_bytes(
        self, data: bytes, *, replace: bool = False, decisions: dict[str, int | None] | None = None
    ) -> GedcomStats:
        return self.apply(self.parse_bytes(data), decisions=decisions, replace=replace)

    def import_text(
        self, text: str, *, replace: bool = False, decisions: dict[str, int | None] | None = None
    ) -> GedcomStats:
        return self.apply(self.parse(text), decisions=decisions, replace=replace)
