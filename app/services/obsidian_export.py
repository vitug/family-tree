"""Экспорт семейного дерева в Markdown-заметки для Obsidian.

Каждый человек — отдельный .md с YAML frontmatter (Properties / Bases)
и wiki-ссылками [[...]] на родственников.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Gender, Media, Person
from app.services.tree_service import TreeService

INVALID_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class ExportStats:
    people: int = 0
    files: int = 0
    photos: int = 0
    export_dir: str = ""
    warnings: list[str] = field(default_factory=list)


def _safe_filename(name: str, max_len: int = 120) -> str:
    name = INVALID_FS.sub("", name).strip().rstrip(".")
    name = re.sub(r"\s+", " ", name)
    if not name:
        name = "person"
    return name[:max_len]


def _yaml_scalar(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    text = str(value)
    # Экранирование для YAML double-quoted
    if any(c in text for c in (":", "#", "{", "}", "[", "]", ",", "&", "*", "!", "|", ">", "'", '"', "%", "@", "`", "\n")) or text != text.strip():
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def _gender_label(g: Gender | str | None) -> str:
    mapping = {
        Gender.MALE: "мужской",
        Gender.FEMALE: "женский",
        Gender.OTHER: "другой",
        Gender.UNKNOWN: "не указан",
        "male": "мужской",
        "female": "женский",
        "other": "другой",
        "unknown": "не указан",
    }
    return mapping.get(g, "не указан")  # type: ignore[arg-type]


class ObsidianExporter:
    """Генерация vault-фрагмента для Obsidian."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.tree = TreeService(db)
        self.media_root = Path(settings.media_dir)

    def export(
        self,
        export_dir: Path | None = None,
        *,
        clean: bool = True,
    ) -> ExportStats:
        out = Path(export_dir or settings.obsidian_export_dir)
        people_dir = out / "People"
        attach_dir = people_dir / "attachments"

        if clean and out.exists():
            shutil.rmtree(out, ignore_errors=True)
        people_dir.mkdir(parents=True, exist_ok=True)
        attach_dir.mkdir(parents=True, exist_ok=True)

        people = list(self.db.scalars(select(Person).order_by(Person.id)).all())
        stats = ExportStats(export_dir=str(out.resolve()))

        if not people:
            stats.warnings.append("В базе нет людей для экспорта")
            self._write_index(out, people_dir, {}, [])
            stats.files += 1
            return stats

        # id → имя заметки (stem без .md)
        note_names: dict[int, str] = {}
        used: set[str] = set()
        for p in people:
            base = _safe_filename(f"{p.id:04d} {p.full_name}")
            name = base
            n = 2
            while name.lower() in used:
                name = f"{base} ({n})"
                n += 1
            used.add(name.lower())
            note_names[p.id] = name

        for p in people:
            rel = self.tree.get_relatives(p.id)
            photo_embed = self._export_photos(p, attach_dir, stats)
            content = self._render_person(p, note_names, rel, photo_embed)
            path = people_dir / f"{note_names[p.id]}.md"
            path.write_text(content, encoding="utf-8")
            stats.people += 1
            stats.files += 1

        self._write_index(out, note_names, people)
        self._write_base(out)
        stats.files += 3  # Семейное дерево.md + README.md + Family Tree.base

        return stats

    def export_zip(self, zip_path: Path | None = None) -> tuple[Path, ExportStats]:
        stats = self.export(clean=True)
        out = Path(stats.export_dir)
        zpath = Path(zip_path or (out.parent / "family_tree_obsidian.zip"))
        if zpath.exists():
            zpath.unlink()
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in out.rglob("*"):
                if file.is_file():
                    zf.write(file, file.relative_to(out).as_posix())
        return zpath, stats

    def _wikilink(self, person_id: int, note_names: dict[int, str], display: str | None = None) -> str:
        name = note_names.get(person_id, str(person_id))
        if display and display != name:
            return f"[[{name}|{display}]]"
        return f"[[{name}]]"

    def _export_photos(self, person: Person, attach_dir: Path, stats: ExportStats) -> list[str]:
        """Копирует фото в attachments, возвращает wiki-пути для ![[...]]."""
        embeds: list[str] = []
        media_rows = list(
            self.db.scalars(
                select(Media).where(Media.person_id == person.id).order_by(Media.is_primary.desc(), Media.id)
            ).all()
        )
        # fallback primary path
        paths: list[tuple[str, str | None]] = []
        if media_rows:
            for m in media_rows:
                paths.append((m.file_path, m.description))
        elif person.photo_path:
            paths.append((person.photo_path, None))

        for file_path, desc in paths:
            src = self.media_root / file_path
            if not src.is_file():
                stats.warnings.append(f"Нет файла фото для id={person.id}: {file_path}")
                continue
            dest_name = _safe_filename(f"{person.id:04d}_{src.name}")
            dest = attach_dir / dest_name
            if not dest.exists():
                shutil.copy2(src, dest)
                stats.photos += 1
            # путь относительно People/*.md
            embeds.append(f"attachments/{dest_name}")
            if desc:
                pass
        return embeds

    def _render_person(
        self,
        p: Person,
        note_names: dict[int, str],
        rel,
        photo_embeds: list[str],
    ) -> str:
        parents_links = [
            self._wikilink(x.id, note_names, x.full_name) for x in rel.parents
        ]
        spouses_links = [
            self._wikilink(x.id, note_names, x.full_name) for x in rel.spouses
        ]
        children_links = [
            self._wikilink(x.id, note_names, x.full_name) for x in rel.children
        ]
        siblings_links = [
            self._wikilink(x.id, note_names, x.full_name) for x in rel.siblings
        ]

        gval = p.gender.value if isinstance(p.gender, Gender) else str(p.gender or "unknown")

        def list_block(key: str, links: list[str]) -> list[str]:
            if not links:
                return [f"{key}: []"]
            lines = [f"{key}:"]
            lines.extend(f"  - {_yaml_scalar(x)}" for x in links)
            return lines

        lines = [
            "---",
            "type: person",
            f"id: {p.id}",
            f"person_id: {p.id}",
            f"full_name: {_yaml_scalar(p.full_name)}",
            f"first_name: {_yaml_scalar(p.first_name)}",
            f"last_name: {_yaml_scalar(p.last_name)}",
            f"middle_name: {_yaml_scalar(p.middle_name)}",
            f"maiden_name: {_yaml_scalar(p.maiden_name)}",
            f"gender: {_yaml_scalar(gval)}",
            f"gender_label: {_yaml_scalar(_gender_label(p.gender))}",
            f"birth_date: {_yaml_scalar(p.birth_date)}",
            f"death_date: {_yaml_scalar(p.death_date)}",
            f"birth_place: {_yaml_scalar(p.birth_place)}",
            f"has_photo: {'true' if photo_embeds else 'false'}",
            f"photo: {_yaml_scalar(photo_embeds[0] if photo_embeds else None)}",
            f"notes: {_yaml_scalar(p.notes)}",
            "tags:",
            "  - family-tree",
            "  - person",
            f"  - {gval}",
            *list_block("parents", parents_links),
            *list_block("spouses", spouses_links),
            *list_block("children", children_links),
            *list_block("siblings", siblings_links),
            "---",
            "",
            f"# {p.full_name}",
            "",
        ]

        if photo_embeds:
            for emb in photo_embeds:
                lines.append(f"![[{emb}]]")
            lines.append("")

        lines.extend(
            [
                "## Данные",
                "",
                "| Поле | Значение |",
                "| --- | --- |",
                f"| ID | `{p.id}` |",
                f"| Имя | {p.first_name} |",
                f"| Фамилия | {p.last_name or '—'} |",
                f"| Отчество | {p.middle_name or '—'} |",
                f"| Девичья фамилия | {p.maiden_name or '—'} |",
                f"| Пол | {_gender_label(p.gender)} |",
                f"| Дата рождения | {p.birth_date.isoformat() if p.birth_date else '—'} |",
                f"| Дата смерти | {p.death_date.isoformat() if p.death_date else '—'} |",
                f"| Место рождения | {p.birth_place or '—'} |",
                "",
            ]
        )

        def section(title: str, links: list[str]) -> None:
            lines.append(f"## {title}")
            lines.append("")
            if not links:
                lines.append("_нет данных_")
            else:
                for link in links:
                    lines.append(f"- {link}")
            lines.append("")

        section("Родители", parents_links)
        section("Супруги", spouses_links)
        section("Дети", children_links)
        section("Братья и сёстры", siblings_links)

        if p.notes:
            lines.extend(["## Заметки", "", p.notes, ""])

        lines.extend(
            [
                "---",
                "← [[Семейное дерево|К оглавлению]]",
                "",
            ]
        )
        return "\n".join(lines)

    def _write_index(
        self,
        out: Path,
        note_names: dict[int, str],
        people: list[Person],
    ) -> None:
        # Оглавление в корне экспорта
        lines = [
            "---",
            "type: family-tree-index",
            "tags:",
            "  - family-tree",
            "  - index",
            f"people_count: {len(people)}",
            "---",
            "",
            "# Семейное дерево",
            "",
            f"Экспорт из Family Tree Service · {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            f"Всего людей: **{len(people)}**",
            "",
            "## База (Obsidian Bases / Dataview)",
            "",
            "Откройте файл `Family Tree.base` для табличного вида, либо Dataview:",
            "",
            "```dataview",
            'TABLE id, gender_label AS Пол, birth_date AS "Рождение", death_date AS "Смерть", birth_place AS "Место"',
            'FROM "People"',
            'WHERE type = "person"',
            "SORT last_name ASC, first_name ASC",
            "```",
            "",
            "## Все люди",
            "",
        ]

        # sort by last name
        sorted_people = sorted(
            people,
            key=lambda x: ((x.last_name or "").lower(), (x.first_name or "").lower(), x.id),
        )
        for p in sorted_people:
            name = note_names.get(p.id, str(p.id))
            dates = []
            if p.birth_date:
                dates.append(f"р. {p.birth_date.year}")
            if p.death_date:
                dates.append(f"† {p.death_date.year}")
            extra = f" — {', '.join(dates)}" if dates else ""
            lines.append(f"- [[{name}|{p.full_name}]]{extra}")

        lines.append("")
        (out / "Семейное дерево.md").write_text("\n".join(lines), encoding="utf-8")

        # README
        readme = [
            "# Экспорт для Obsidian",
            "",
            "1. Откройте эту папку как vault **или** скопируйте `People/` и `Семейное дерево.md` в существующий vault.",
            "2. Включите плагин **Dataview** (Community) — запросы уже в оглавлении.",
            "3. Для **Bases** откройте `Family Tree.base` (Obsidian 1.9+).",
            "4. Ссылки вида `[[0001 Имя]]` ведут к заметкам родственников.",
            "5. Фото лежат в `People/attachments/`.",
            "",
        ]
        (out / "README.md").write_text("\n".join(readme), encoding="utf-8")

    def _write_base(self, out: Path) -> None:
        """Файл Obsidian Bases (таблица людей по свойствам)."""
        # Формат .base — YAML, поддерживаемый Obsidian Bases
        content = """\
filters:
  and:
    - file.inFolder("People")
    - 'type == "person"'
views:
  - type: table
    name: Все люди
    order:
      - file.name
      - last_name
      - first_name
      - gender_label
      - birth_date
      - death_date
      - birth_place
    sort:
      - property: last_name
        direction: ASC
      - property: first_name
        direction: ASC
  - type: table
    name: С фото
    filters:
      and:
        - 'has_photo == true'
    order:
      - file.name
      - full_name
      - photo
  - type: cards
    name: Карточки
    order:
      - file.name
      - full_name
      - birth_date
"""
        (out / "Family Tree.base").write_text(content, encoding="utf-8")
