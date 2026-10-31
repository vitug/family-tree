"""Экспорт семейного дерева в Gramps XML (.gramps).

Формат — Gramps XML 1.7.0 (plain, без gzip): Gramps импортирует его
напрямую (Family Trees → Import). Отчество сохраняется в <first>
(«Иван Иванович» — у Gramps нет отдельного поля отчества), девичья
фамилия — вторым именем типа Birth Name, текущая — Married Name.
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.etree.ElementTree import Element, SubElement, indent, tostring

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Gender, Media, Person, RelationType, Relationship

HEADER_CREATED_WITH = "Family Tree Service"


def _x(parent: Element, tag: str, text: str | None = None, **attrs) -> Element:
    el = SubElement(parent, tag)
    if text is not None:
        el.text = text
    for k, v in attrs.items():
        el.set(k, v)
    return el


def _esc_text(s: str) -> str:
    return s


@dataclass
class ExportStats:
    people: int = 0
    families: int = 0
    relationships: int = 0
    media: int = 0


class GrampsExporter:
    """Собирает Gramps XML из людей, связей и фото в базе."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # handle/id генераторы: уникальные в пределах файла
    @staticmethod
    def _person_handle(pid: int) -> str:
        return f"_FT{pid:07X}"

    @staticmethod
    def _person_id(pid: int) -> str:
        return f"I{pid:07d}"

    def export_bytes(self) -> tuple[bytes, ExportStats]:
        stats = ExportStats()

        persons: list[Person] = list(
            self.db.scalars(select(Person).order_by(Person.id)).all()
        )
        rels: list[Relationship] = list(self.db.scalars(select(Relationship)).all())
        medias: list[Media] = list(self.db.scalars(select(Media)).all())

        # индексы
        spouse_pairs: set[tuple[int, int]] = set()
        parent_of: dict[int, list[int]] = {}  # parent_id -> [child_id]
        for r in rels:
            if r.relation_type == RelationType.SPOUSE_OF:
                spouse_pairs.add(tuple(sorted((r.person_id, r.related_person_id))))
            elif r.relation_type == RelationType.PARENT_OF:
                parent_of.setdefault(r.person_id, []).append(r.related_person_id)
        children_of_pair: dict[tuple[int, int], list[int]] = {}
        # ребёнок в семью — если оба супруга его родители, иначе к одному
        for parent, kids in parent_of.items():
            for kid in kids:
                pair = next(
                    (
                        p
                        for p in spouse_pairs
                        if parent in p and kid in parent_of.get(p[0] if p[0] != parent else p[1], [])
                    ),
                    None,
                )
                key = pair if pair else (parent, parent)
                lst = children_of_pair.setdefault(key, [])
                if kid not in lst:
                    lst.append(kid)

        media_by_person: dict[int, list[Media]] = {}
        for m in medias:
            if m.person_id:
                media_by_person.setdefault(m.person_id, []).append(m)
        stats.media = len(medias)

        person_by_id = {p.id: p for p in persons}
        gender_of = {p.id: p.gender for p in persons}

        root = Element("database", {"xmlns": "http://gramps-project.org/xml/1.7.0"})

        header = _x(root, "header")
        _x(header, "created", date="2026", version="1.7.0")
        _x(header, "researcher")
        _x(header, "mediapath")

        # ── places: уникальные места рождения ──────────────────────
        place_titles = sorted({p.birth_place for p in persons if p.birth_place})
        place_handle = {t: f"_FTPL{i:05X}" for i, t in enumerate(place_titles)}
        if place_titles:
            places_el = _x(root, "places")
            for i, t in enumerate(place_titles):
                pobj = _x(places_el, "placeobj", handle=place_handle[t], type="Unknown", id=f"P{i:05d}")
                _x(pobj, "ptitle", t)

        # ── events: рождение/смерть по человеку ────────────────────
        events_el = _x(root, "events")
        ev_handle: dict[tuple[int, str], str] = {}
        ev_counter = 0
        for p in persons:
            for kind, dt in (("Birth", p.birth_date), ("Death", p.death_date)):
                if dt is None and kind == "Death":
                    continue
                if dt is None and kind == "Birth" and not p.birth_place:
                    continue
                h = f"_FTE{ev_counter:06X}"
                ev_counter += 1
                ev_handle[(p.id, kind)] = h
                ev = _x(events_el, "event", handle=h, id=f"E{ev_counter:06d}")
                _x(ev, "type", kind)
                if dt is not None:
                    _x(ev, "dateval", val=dt.isoformat())
                if kind == "Birth" and p.birth_place:
                    _x(ev, "place", hlink=place_handle[p.birth_place])

        # ── notes ──────────────────────────────────────────────────
        note_persons = [p for p in persons if p.notes]
        note_handle: dict[int, str] = {p.id: f"_FTN{i:05X}" for i, p in enumerate(note_persons)}
        if note_persons:
            notes_el = _x(root, "notes")
            for i, p in enumerate(note_persons):
                _x(notes_el, "note", p.notes, handle=f"_FTN{i:05X}", type="Person Note")

        # ── objects (фото) ─────────────────────────────────────────
        if medias:
            objects_el = _x(root, "objects")
            for i, m in enumerate(medias):
                obj = _x(objects_el, "object", handle=f"_FTO{i:06X}", id=f"O{i:06d}")
                fname = (m.file_path or "").rsplit("/", 1)[-1] or f"photo{i}.jpg"
                _x(
                    obj,
                    "file",
                    src=f"media/files/{m.file_path or ''}",
                    mime=m.mime_type or "image/jpeg",
                    description=m.description or fname,
                )

        # ── people ─────────────────────────────────────────────────
        people_el = _x(root, "people")
        media_handles = {m.id: f"_FTO{i:06X}" for i, m in enumerate(medias)}
        for p in persons:
            stats.people += 1
            pel = _x(
                people_el,
                "person",
                handle=self._person_handle(p.id),
                id=self._person_id(p.id),
            )
            g = {Gender.MALE: "M", Gender.FEMALE: "F", Gender.OTHER: "X"}.get(p.gender, "U")
            _x(pel, "gender", g)

            first_full = " ".join(x for x in (p.first_name, p.middle_name) if x)
            if p.maiden_name:
                n = _x(pel, "name", type="Birth Name")
                _x(n, "first", first_full)
                _x(n, "surname", p.maiden_name)
                nm = _x(pel, "name", type="Married Name")
                _x(nm, "first", first_full)
                _x(nm, "surname", p.last_name)
            else:
                n = _x(pel, "name", type="Birth Name")
                _x(n, "first", first_full)
                _x(n, "surname", p.last_name)

            if p.birth_date is not None or p.birth_place:
                h = ev_handle.get((p.id, "Birth"))
                if h:
                    _x(pel, "eventref", hlink=h, role="Primary")
            if p.death_date is not None:
                h = ev_handle.get((p.id, "Death"))
                if h:
                    _x(pel, "eventref", hlink=h, role="Primary")
            if p.id in note_handle:
                _x(pel, "noteref", hlink=note_handle[p.id])
            for m in media_by_person.get(p.id, []):
                objref = _x(pel, "objref", hlink=media_handles[m.id])
                if m.is_primary:
                    _x(objref, "region", type="rectangle", corner1="0.0,0.0", corner2="1.0,1.0")

        # ── families ───────────────────────────────────────────────
        used_pairs = set()
        fam_el = None
        for pair in sorted(spouse_pairs):
            used_pairs.add(pair)
            stats.families += 1
            a, b = person_by_id[pair[0]], person_by_id[pair[1]]
            fam_el = _x(root, "families") if fam_el is None else fam_el
            fam = _x(fam_el, "family", handle=f"_FTF{stats.families:06X}", id=f"F{stats.families:06d}")
            rel_id = "Married" if any(
                r.relation_type == RelationType.SPOUSE_OF
                and tuple(sorted((r.person_id, r.related_person_id))) == pair
                for r in rels
            ) else "Unknown"
            _x(fam, "rel", type=rel_id)
            father, mother = (a, b) if gender_of.get(a.id) != Gender.FEMALE else (b, a)
            if gender_of.get(father.id) != Gender.FEMALE:
                _x(fam, "father", hlink=self._person_handle(father.id))
                _x(fam, "mother", hlink=self._person_handle(mother.id))
            else:
                _x(fam, "mother", hlink=self._person_handle(mother.id))
            for kid in children_of_pair.get(pair, []):
                _x(fam, "childref", hlink=self._person_handle(kid))

        # одиночные родители (без супруга) — своя семья
        for parent, kids in sorted(parent_of.items()):
            covered = any(parent in pr for pr in used_pairs)
            if covered:
                continue
            stats.families += 1
            fam_el = _x(root, "families") if fam_el is None else fam_el
            fam = _x(fam_el, "family", handle=f"_FTF{stats.families:06X}", id=f"F{stats.families:06d}")
            _x(fam, "rel", type="Unknown")
            if gender_of.get(parent) == Gender.FEMALE:
                _x(fam, "mother", hlink=self._person_handle(parent))
            else:
                _x(fam, "father", hlink=self._person_handle(parent))
            for kid in kids:
                _x(fam, "childref", hlink=self._person_handle(kid))

        stats.relationships = len(rels)

        indent(root)
        xml_bytes = b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode").encode("utf-8")
        return xml_bytes, stats

    def export_gpkg(self) -> tuple[bytes, ExportStats]:
        """Собрать .gpkg: gzip-tar с data.gramps (gzip XML) + фото из media/files.

        Структура как у настоящего экспорта Gramps — импортируется в Gramps
        напрямую и содержит физические файлы фото."""
        import gzip as _gzip
        import io as _io
        import tarfile as _tarfile
        from pathlib import Path as _Path

        from app.config import settings

        xml_bytes, stats = self.export_bytes()

        buf = _io.BytesIO()
        with _tarfile.open(fileobj=buf, mode="w") as tf:
            def _add(name: str, data: bytes) -> None:
                ti = _tarfile.TarInfo(name)
                ti.size = len(data)
                tf.addfile(ti, _io.BytesIO(data))

            _add("data.gramps", _gzip.compress(xml_bytes))
            media_root = _Path(settings.media_dir)
            for m in self.db.scalars(select(Media)).all():
                if not m.file_path:
                    continue
                fp = media_root / m.file_path
                if fp.is_file():
                    _add(str(_Path(*fp.parts[-2:])), fp.read_bytes())
        return _gzip.compress(buf.getvalue()), stats
