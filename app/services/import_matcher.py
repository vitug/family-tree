"""Матчинг импортируемых людей с существующими записями базы.

Два уровня уверенности:
1. external — точное совпадение внешнего ID (Gramps ID / GEDCOM xref)
2. attributes — совпадение нормализованных ФИО + даты рождения (или дат без
   рождения, если у обеих сторон пусто)

Решение всегда принимает человек: preview отдаёт кандидатов, редактор
связей в UI позволяет выбрать «новый» или конкретную запись.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.models import Person, PersonExternalRef


def _norm(s: str | None) -> str:
    return (s or "").strip().lower().replace("ё", "е")


@dataclass
class MatchCandidate:
    person_id: int
    full_name: str
    birth_date: date | None
    reason: str  # "external" | "attributes"
    detail: str


@dataclass
class ParsedPerson:
    """Человек из файла импорта, ещё не записанный в базу."""

    key: str  # внешний ID (xref / gramps id) или синтетический
    first_name: str
    last_name: str | None = None
    middle_name: str | None = None
    maiden_name: str | None = None
    gender: str = "unknown"
    birth_date: date | None = None
    death_date: date | None = None
    birth_place: str | None = None
    notes: str | None = None

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.last_name, self.first_name, self.middle_name) if p]
        return " ".join(parts) or self.key


def find_candidates(db: Session, person: ParsedPerson, source: str) -> list[MatchCandidate]:
    """Кандидаты на склейку, отсортированные по уверенности."""
    out: list[MatchCandidate] = []

    # 1. точное совпадение внешнего ID того же источника
    if person.key:
        ref = (
            db.query(PersonExternalRef)
            .filter_by(source=source, external_id=person.key[:64])
            .first()
        )
        if ref is not None:
            p = db.get(Person, ref.person_id)
            if p is not None:
                out.append(
                    MatchCandidate(
                        person_id=p.id,
                        full_name=p.full_name,
                        birth_date=p.birth_date,
                        reason="external",
                        detail=f"{source} ID {person.key}",
                    )
                )

    # 2. совпадение по атрибутам: Ф + И (+ отчество, если есть у обоих).
    # SQLite LIKE/lower нечувствительны к регистру только для ASCII, поэтому
    # фильтруем в Python (объём семейной базы — десятки/сотни записей).
    first, last = _norm(person.first_name), _norm(person.last_name)
    middle = _norm(person.middle_name)
    if first and last:
        for p in db.query(Person).limit(5000):
            pm = _norm(p.middle_name)
            if middle and pm and middle != pm:
                continue
            if not middle and pm:
                continue  # в базе отчество есть, в файле нет — сомнительно
            if person.birth_date and p.birth_date and person.birth_date != p.birth_date:
                continue  # разные даты рождения — разные люди
            if any(c.person_id == p.id for c in out):
                continue
            date_note = (
                f"род. {person.birth_date.isoformat()}" if person.birth_date else "дата рождения не указана"
            )
            out.append(
                MatchCandidate(
                    person_id=p.id,
                    full_name=p.full_name,
                    birth_date=p.birth_date,
                    reason="attributes",
                    detail=f"совпадение имени; {date_note}",
                )
            )
    return out
