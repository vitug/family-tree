"""Экспорт семейного дерева в формат Karpathy LLM-wiki.

Структура архива:
    SCHEMA.md          — конвенции wiki (домен: семейное дерево)
    index.md           — каталог страниц entities/
    log.md             — запись о создании
    entities/<slug>.md — страница на человека: frontmatter, факты,
                         связи с родственниками через [[wikilinks]]

`[[wikilink]]` указывает на stem файла страницы родственника, поэтому
экспорт открывается в Obsidian и проходит wiki_health_check.py.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Media, Person, Relationship


@dataclass
class WikiExportStats:
    people: int = 0
    files: int = 0
    links: int = 0
    warnings: list[str] = field(default_factory=list)


_SLUG_STRIP = '<>:"/\\|?*#^[]'


def _slugify(name: str) -> str:
    for ch in _SLUG_STRIP:
        name = name.replace(ch, "")
    return name.strip().replace(" ", "-")


def _fm_date(d: date | None) -> str:
    return d.isoformat() if d else ""


class WikiExporter:
    """Собирает LLM-wiki из людей и связей базы."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.stats = WikiExportStats()

    # ── ключевые данные ─────────────────────────────────────────

    def _load(self) -> tuple[list[Person], dict[int, list[tuple[str, Person]]]]:
        people = list(self.db.scalars(select(Person)))
        rels = list(self.db.scalars(select(Relationship)))
        by_id = {p.id: p for p in people}

        # родственники каждого человека: (метка связи, человек).
        # parent_of направленная: person_id — родитель, related_person_id — ребёнок.
        related: dict[int, list[tuple[str, Person]]] = {p.id: [] for p in people}
        for r in rels:
            parent, child = by_id.get(r.person_id), by_id.get(r.related_person_id)
            if parent is None or child is None:
                continue
            rtype = r.relation_type.value if hasattr(r.relation_type, "value") else str(r.relation_type)
            if rtype == "parent_of":
                related[parent.id].append(("дети", child))
                related[child.id].append(("родители", parent))
            else:  # spouse_of и прочие симметричные
                related[parent.id].append(("супруг(а)", child))
                related[child.id].append(("супруг(а)", parent))
        return people, related

    def _media_by_person(self) -> dict[int, list[Media]]:
        out: dict[int, list[Media]] = {}
        for m in self.db.scalars(select(Media)):
            if m.person_id:
                out.setdefault(m.person_id, []).append(m)
        return out

    # ── страницы ────────────────────────────────────────────────

    def _slug_map(self, people: list[Person]) -> dict[int, str]:
        slugs: dict[int, str] = {}
        used: set[str] = set()
        for p in sorted(people, key=lambda x: x.id):
            base = _slugify(p.full_name or f"person-{p.id}")
            if not base or base in used:
                base = f"{base or 'person'}-{p.id}"
            slugs[p.id] = base
            used.add(base)
        return slugs

    def _person_page(self, p: Person, rels: list[tuple[str, Person]], slugs: dict[int, str]) -> str:
        lines: list[str] = []
        updated = _fm_date(getattr(p, "updated_at", None))
        if hasattr(updated, "date"):
            updated = updated.date().isoformat()
        lines += [
            "---",
            f"title: {p.full_name or f'Человек #{p.id}'}",
            f"created: {_fm_date(p.birth_date) or date.today().isoformat()}",
            f"updated: {updated or 'unknown'}",
            "type: entity",
            f"tags: [person, {(p.gender or 'unknown').lower()}]",
            "sources: []",
            "---",
            "",
            f"# {p.full_name or f'Человек #{p.id}'}",
            "",
        ]
        facts: list[str] = []
        if p.birth_date:
            facts.append(f"- **Дата рождения:** {p.birth_date.isoformat()}")
        if p.death_date:
            facts.append(f"- **Дата смерти:** {p.death_date.isoformat()}")
        if getattr(p, "birth_place", None):
            facts.append(f"- **Место рождения:** {p.birth_place}")
        if getattr(p, "maiden_name", None):
            facts.append(f"- **Девичья фамилия:** {p.maiden_name}")
        lines += (facts or ["- (дат и мест не заполнено)"]) + [""]

        grouped: dict[str, list[Person]] = {}
        for rtype, other in rels:
            grouped.setdefault(rtype, []).append(other)
        if grouped:
            lines.append("## Связи")
            for rtype, others in sorted(grouped.items()):
                lst = ", ".join(f"[[{slugs[o.id]}]]" for o in sorted(others, key=lambda x: x.id))
                self.stats.links += len(others)
                lines.append(f"- **{rtype}** — {lst}")
            lines.append("")

        if p.notes:
            lines += ["## Заметки", "", p.notes.strip(), ""]

        media = self._media_map().get(p.id, [])
        if media:
            lines.append("## Фото")
            for m in media:
                lines.append(f"- `{m.file_path}`{' (главное)' if m.is_primary else ''}")
            lines.append("")

        if len(lines) and not grouped and not p.notes and not media:
            lines.append("(нет дополнительной информации)")
        return "\n".join(lines).rstrip() + "\n"

    _media_cache: dict[int, list[Media]] | None = None

    def _media_map(self) -> dict[int, list[Media]]:
        if self._media_cache is None:
            self._media_cache = self._media_by_person()
        return self._media_cache

    # ── архив ───────────────────────────────────────────────────

    def export_zip(self) -> tuple[bytes, WikiExportStats]:
        people, related = self._load()
        slugs = self._slug_map(people)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            self._write(zf, "SCHEMA.md", self._schema_md(len(people)))
            self._write(zf, "index.md", self._index_md(people, slugs))
            for p in people:
                page = self._person_page(p, related[p.id], slugs)
                self._write(zf, f"entities/{slugs[p.id]}.md", page)
            self._write(zf, "log.md", self._log_md(len(people)))
        self.stats.people = len(people)
        return buf.getvalue(), self.stats

    def _write(self, zf: zipfile.ZipFile, name: str, text: str) -> None:
        zf.writestr(name, text)
        self.stats.files += 1

    def _schema_md(self, n: int) -> str:
        return f"""# Wiki Schema

## Domain
Семейное дерево: люди, даты, места, родственные связи. Сгенерировано
экспортом `GET /api/v1/export/wiki` из приложения family-tree
({n} человек на момент экспорта).

## Conventions
- Страницы людей лежат в `entities/`, имя файла = stem для wiki-ссылок в двойных квадратных скобках.
- Frontmatter обязателен: title, created, updated, type, tags, sources.
- Связи описываются списком «тип связи» и wiki-ссылкой на страницу в двойных квадратных скобках.
- `raw/` в этом экспорте пуст: первоисточник — база приложения.

## Tag Taxonomy
- person, male, female, unknown
- family, generation, photo
"""

    def _index_md(self, people: list[Person], slugs: dict[int, str]) -> str:
        lines = [
            "# Wiki Index",
            "",
            f"> Каталог страниц семейного дерева. Всего людей: {len(people)}.",
            "",
            "## Entities",
            "",
        ]
        for p in sorted(people, key=lambda x: (x.full_name or "", x.id)):
            summary = p.full_name or f"Человек #{p.id}"
            if p.birth_date:
                summary += f" (р. {p.birth_date.year})"
            lines.append(f"- [[{slugs[p.id]}]] — {summary}")
        return "\n".join(lines) + "\n"

    def _log_md(self, n: int) -> str:
        return f"""# Wiki Log

## [{date.today().isoformat()}] create | Wiki сгенерирована экспортом
- Экспорт семейного дерева: {n} человек
- Источник: приложение family-tree (SQLite)
"""


def _inverse(rtype: str) -> str:
    inv = {
        "parent": "child",
        "child": "parent",
        "spouse": "spouse",
        "sibling": "sibling",
        "мать": "ребёнок",
        "отец": "ребёнок",
        "ребёнок": "родитель",
        "родитель": "ребёнок",
        "супруг": "супруг",
        "супруга": "супруг",
        "брат": "брат/сестра",
        "сестра": "брат/сестра",
    }
    return inv.get(rtype, rtype)
