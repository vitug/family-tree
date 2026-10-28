"""Экспорт семейного дерева в GEDCOM 5.5.1.

INDI: NAME (текущее), при наличии девичьей фамилии — второе NAME TYPE birth,
SEX, BIRT (DATE/PLAC), DEAT, NOTE, FAMC/FAMS.
FAM: группы детей по набору родителей + супругские пары без детей.
Кодировка UTF-8 (широко поддерживаемое расширение 5.5.1), концы строк LF.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Gender, Person, RelationType, Relationship

_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
           "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


@dataclass
class GedcomExportStats:
    people: int = 0
    families: int = 0
    links: int = 0
    warnings: list[str] = field(default_factory=list)


def _gdate(d: date) -> str:
    return f"{d.day:02d} {_MONTHS[d.month - 1]} {d.year}"


def _clean(text: str) -> str:
    """GEDCOM-строка: без переносов внутри значения."""
    return " ".join(text.split())


class GedcomExporter:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.stats = GedcomExportStats()

    def export_text(self) -> tuple[str, GedcomExportStats]:
        people = list(self.db.scalars(select(Person)))
        rels = list(self.db.scalars(select(Relationship)))
        if not people:
            return "", self.stats

        by_id = {p.id: p for p in people}

        # ребёнок -> его родители (parent_of)
        parents_of: dict[int, list[Person]] = {}
        # супругские пары (spouse_of, хранится в одном направлении)
        spouse_pairs: list[tuple[Person, Person]] = []
        for r in rels:
            a, b = by_id.get(r.person_id), by_id.get(r.related_person_id)
            if a is None or b is None:
                continue
            if r.relation_type == RelationType.PARENT_OF:
                parents_of.setdefault(b.id, []).append(a)
            else:
                spouse_pairs.append((a, b))

        # Семьи: уникальный ключ = отсортированный набор id родителей.
        fams: dict[tuple[int, ...], dict] = {}

        def get_fam(parent_ids: tuple[int, ...]) -> dict:
            if parent_ids not in fams:
                fams[parent_ids] = {"parents": [by_id[i] for i in parent_ids], "children": []}
            return fams[parent_ids]

        for child_id, parents in parents_of.items():
            key = tuple(sorted(p.id for p in parents))
            fam = get_fam(key)
            if child_id not in [c.id for c in fam["children"]]:
                fam["children"].append(by_id[child_id])

        # супругские пары без детей — отдельные FAM (чтобы у супругов были FAMS)
        for a, b in spouse_pairs:
            key = tuple(sorted({a.id, b.id}))
            if key not in fams:
                get_fam(key)

        self.stats.families = len(fams)
        self.stats.people = len(people)

        lines: list[str] = []
        lines += [
            "0 HEAD",
            "1 SOUR family-tree",
            "1 GEDC",
            "2 VERS 5.5.1",
            "2 FORM LINEAGE-LINKED",
            "1 CHAR UTF-8",
            f"1 DATE {date.today().strftime('%d %b %Y').upper()}",
        ]

        fam_no = {key: i + 1 for i, key in enumerate(sorted(fams))}

        for p in sorted(people, key=lambda x: x.id):
            lines.append(f"0 @{p.id}@ INDI")
            first = _clean(p.first_name or "")
            last = _clean(p.last_name or "")
            lines.append(f"1 NAME {first} /{last}/")
            if p.maiden_name:
                lines.append(f"1 NAME {first} /{_clean(p.maiden_name)}/")
                lines.append("2 TYPE birth")
            g = p.gender or Gender.UNKNOWN
            sex = {Gender.MALE: "M", Gender.FEMALE: "F"}.get(g, "U")
            lines.append(f"1 SEX {sex}")
            if p.birth_date or p.birth_place:
                lines.append("1 BIRT")
                if p.birth_date:
                    lines.append(f"2 DATE {_gdate(p.birth_date)}")
                if p.birth_place:
                    lines.append(f"2 PLAC {_clean(p.birth_place)}")
            if p.death_date:
                lines.append("1 DEAT")
                lines.append(f"2 DATE {_gdate(p.death_date)}")
            if p.notes:
                lines += _cont_lines(_split_note(p.notes))
            # FAMC: семьи, где человек — ребёнок
            for key, fam in fams.items():
                if any(c.id == p.id for c in fam["children"]):
                    lines.append(f"1 FAMC @{fam_no[key]}@")
            # FAMS: семьи, где человек — родитель или супруг
            for key, fam in fams.items():
                ids = {x.id for x in fam["parents"]}
                if p.id in ids:
                    lines.append(f"1 FAMS @{fam_no[key]}@")

        for key in sorted(fams):
            fam = fams[key]
            lines.append(f"0 @F{fam_no[key]}@ FAM")
            for parent in sorted(fam["parents"], key=lambda x: x.id):
                tag = "HUSB" if (parent.gender or Gender.UNKNOWN) == Gender.MALE else "WIFE"
                lines.append(f"1 {tag} @{parent.id}@")
            for child in sorted(fam["children"], key=lambda x: x.id):
                lines.append(f"1 CHIL @{child.id}@")

        lines.append("0 TRLR")
        self.stats.links = sum(
            2 * len(f["parents"]) + len(f["children"]) for f in fams.values()
        )
        return "\n".join(lines) + "\n", self.stats


def _split_note(note: str, width: int = 200) -> list[str]:
    """NOTE-значения без переносов; длинные режем на CONT-продолжения."""
    out: list[str] = []
    for para in note.splitlines():
        para = _clean(para)
        while len(para) > width:
            cut = para.rfind(" ", 0, width)
            cut = cut if cut > 0 else width
            out.append(para[:cut])
            para = para[cut:].strip()
        if para:
            out.append(para)
    return out or [""]


def _cont_lines(chunks: list[str]) -> list[str]:
    """Первый кусок идёт в NOTE, остальные — CONT."""
    res = []
    for i, c in enumerate(chunks):
        res.append(("1 NOTE " if i == 0 else "2 CONT ") + c)
    return res
