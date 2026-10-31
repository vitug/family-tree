"""Импорт семейного дерева из формата Gramps (.gpkg / .gramps).

.gpkg — gzip-архив tar с data.gramps (gzip-сжатый XML) и медиафайлами.
.gramps — Gramps XML (plain или gzip).

Фото, физически лежащие в .gpkg, сохраняются в media/files и
привязываются к людям по <objref>.
"""

from __future__ import annotations

import hashlib
import io
import re
import shutil
import tarfile
import zlib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from defusedxml import ElementTree as SafeET
from xml.etree import ElementTree as ET

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Gender, Media, Person, RelationType, Relationship

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".bmp"}

# Лимит на распакованный gzip-поток: иначе малый входной файл
# (zip-bomb) раздувался в память без ограничения.
MAX_DECOMPRESSED_SIZE = 256 * 1024 * 1024  # 256 МБ
_CHUNK = 1024 * 1024


def _gzip_decompress_limited(data: bytes) -> bytes:
    """gzip.decompress с лимитом объёма на выходе (анти zip-bomb)."""
    zobj = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = bytearray()
    buf: bytes = data
    while buf:
        piece = zobj.decompress(buf, _CHUNK)
        out += piece
        if len(out) > MAX_DECOMPRESSED_SIZE:
            raise ValueError(
                f"Распакованный размер превышает лимит {MAX_DECOMPRESSED_SIZE // 1024 // 1024} МБ"
            )
        buf = zobj.unconsumed_tail
        if not buf and piece:
            break
    out += zobj.flush()
    if len(out) > MAX_DECOMPRESSED_SIZE:
        raise ValueError(
            f"Распакованный размер превышает лимит {MAX_DECOMPRESSED_SIZE // 1024 // 1024} МБ"
        )
    return bytes(out)


def _gzip_try_decompress(data: bytes) -> bytes:
    """Распаковать gzip с лимитом; если не gzip — вернуть как есть."""
    try:
        return _gzip_decompress_limited(data)
    except (OSError, zlib.error):
        return data


@dataclass
class ImportStats:
    people: int = 0
    families: int = 0
    relationships: int = 0
    skipped_people: int = 0
    media_files: int = 0  # файлов медиа в архиве
    media_imported: int = 0  # уникальных файлов сохранено
    media_linked: int = 0  # привязок person↔photo
    media_missing: int = 0  # object в XML без файла в архиве
    warnings: list[str] = field(default_factory=list)


@dataclass
class PackageContents:
    xml_bytes: bytes
    # Нормализованный ключ пути → содержимое
    media_by_path: dict[str, bytes] = field(default_factory=dict)
    # basename (lower) → содержимое (первый встретившийся)
    media_by_name: dict[str, bytes] = field(default_factory=dict)


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _find(el: ET.Element, name: str) -> ET.Element | None:
    for child in el:
        if _local(child.tag) == name:
            return child
    return None


def _findall(el: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in el if _local(c.tag) == name]


def _text(el: ET.Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    t = el.text.strip()
    return t or None


def _normalize_path(path: str) -> str:
    """Единый вид пути: / как разделитель, без ведущих ./, lower."""
    p = path.replace("\\", "/").strip()
    # убрать drive letter Windows: D:/Загрузки/...
    if len(p) >= 2 and p[1] == ":":
        p = p[2:]
    p = p.lstrip("/")
    while p.startswith("./"):
        p = p[2:]
    return p.lower()


def _basename_key(path: str) -> str:
    return Path(path.replace("\\", "/")).name.lower()


def _looks_like_gzip(data: bytes) -> bool:
    return len(data) >= 2 and data[0] == 0x1F and data[1] == 0x8B


def _looks_like_tar(data: bytes) -> bool:
    if len(data) >= 262 and data[257:262] in (b"ustar", b"ustar\x00"):
        return True
    if data.startswith(b"././@PaxHeader") or data.startswith(b"././@LongLink"):
        return True
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tf:
            tf.getmembers()
        return True
    except tarfile.TarError:
        return False


def _looks_like_xml(data: bytes) -> bool:
    head = data.lstrip()[:200]
    return head.startswith(b"<?xml") or head.startswith(b"<database")


def _is_media_filename(name: str) -> bool:
    return Path(name).suffix.lower() in IMAGE_EXTENSIONS


def _add_media_blob(pkg: PackageContents, archive_path: str, content: bytes) -> None:
    if not content:
        return
    key = _normalize_path(archive_path)
    base = _basename_key(archive_path)
    # не перезаписываем уже сохранённый контент тем же путём
    if key not in pkg.media_by_path:
        pkg.media_by_path[key] = content
    if base and base not in pkg.media_by_name:
        pkg.media_by_name[base] = content


def _extract_from_tar(tar_bytes: bytes) -> PackageContents:
    pkg = PackageContents(xml_bytes=b"")
    xml_candidate: bytes | None = None

    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            extracted = tf.extractfile(member)
            if extracted is None:
                continue
            content = extracted.read()
            base = Path(member.name).name.lower()

            if base.endswith(".gramps") or base == "data.gramps" or base.endswith(".xml"):
                blob = content
                if _looks_like_gzip(blob):
                    blob = _gzip_decompress_limited(blob)
                if _looks_like_xml(blob):
                    xml_candidate = blob
                continue

            if _is_media_filename(member.name) or _is_media_filename(base):
                _add_media_blob(pkg, member.name, content)

    if xml_candidate is None:
        raise ValueError("В архиве .gpkg не найден data.gramps / Gramps XML")
    pkg.xml_bytes = xml_candidate
    return pkg


def extract_package(data: bytes, filename: str = "") -> PackageContents:
    """Извлечь XML и медиафайлы из .gpkg / .gramps / XML."""
    name = filename.lower()

    if name.endswith(".gpkg") or _looks_like_gzip(data):
        decompressed = _gzip_try_decompress(data)

        if _looks_like_tar(decompressed):
            return _extract_from_tar(decompressed)

        if _looks_like_xml(decompressed):
            return PackageContents(xml_bytes=decompressed)

        inner = _gzip_try_decompress(decompressed)
        if _looks_like_xml(inner):
            return PackageContents(xml_bytes=inner)
        data = decompressed

    if _looks_like_xml(data):
        return PackageContents(xml_bytes=data)

    if _looks_like_tar(data):
        return _extract_from_tar(data)

    raise ValueError(
        "Не удалось распознать файл как Gramps .gpkg / .gramps / XML. "
        "Ожидается пакет Gramps или файл data.gramps."
    )


def parse_gramps_date(value: str | None) -> date | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None

    if re.fullmatch(r"\d{4}", value):
        return date(int(value), 1, 1)

    m = re.fullmatch(r"(\d{4})-(\d{1,2})", value)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        return date(y, mo, 1)

    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d)
        except ValueError:
            return None

    m = re.search(r"\b(\d{4})\b", value)
    if m:
        return date(int(m.group(1)), 1, 1)
    return None


def _map_gender(raw: str | None) -> Gender:
    if not raw:
        return Gender.UNKNOWN
    g = raw.strip().upper()
    if g in ("M", "MALE"):
        return Gender.MALE
    if g in ("F", "FEMALE"):
        return Gender.FEMALE
    if g in ("X", "O", "OTHER", "U"):
        return Gender.OTHER if g != "U" else Gender.UNKNOWN
    return Gender.UNKNOWN


def _guess_mime(path: str, declared: str | None) -> str | None:
    if declared and declared not in ("неизвестен", "unknown", "?"):
        return declared
    ext = Path(path).suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".bmp": "image/bmp",
    }.get(ext)


def _safe_filename(name: str) -> str:
    name = Path(name.replace("\\", "/")).name
    name = re.sub(r"[^\w.\-а-яА-ЯёЁ]+", "_", name, flags=re.UNICODE)
    return name[:120] or "photo.jpg"


class GrampsImporter:
    """Парсит Gramps XML и записывает людей, связи и доступные фото."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.media_root = Path(settings.media_dir)
        self.files_dir = self.media_root / "files"

    def import_bytes(
        self,
        data: bytes,
        *,
        filename: str = "",
        replace: bool = False,
    ) -> ImportStats:
        package = extract_package(data, filename)
        return self.import_package(package, replace=replace)

    def import_package(self, package: PackageContents, *, replace: bool = False) -> ImportStats:
        try:
            root = SafeET.fromstring(package.xml_bytes)
        except ET.ParseError as exc:
            raise ValueError(f"Некорректный Gramps XML: {exc}") from exc

        if _local(root.tag) != "database":
            raise ValueError("Корневой элемент XML должен быть <database>")

        stats = ImportStats()
        stats.media_files = len(package.media_by_path)

        if replace:
            self._clear_all()

        self.files_dir.mkdir(parents=True, exist_ok=True)

        events = self._parse_events(root)
        places = self._parse_places(root)
        # handle object → meta + blob (если файл есть в архиве)
        object_map = self._parse_objects(root, package, stats)
        handle_to_person = self._import_people(root, events, places, stats)
        self._import_families(root, handle_to_person, stats)
        self._link_media(root, handle_to_person, object_map, stats)

        self.db.commit()

        if stats.media_files and stats.media_imported == 0:
            stats.warnings.append(
                f"В архиве {stats.media_files} медиафайл(ов), но ни один не удалось сопоставить с XML."
            )
        elif stats.media_missing:
            stats.warnings.append(
                f"В XML указано медиа без файла в архиве: {stats.media_missing} "
                f"(импортированы только физически присутствующие фото)."
            )
        return stats

    def _clear_all(self) -> None:
        self.db.execute(delete(Media))
        self.db.execute(delete(Relationship))
        self.db.execute(delete(Person))
        self.db.commit()
        # Очистить сохранённые файлы
        if self.files_dir.exists():
            shutil.rmtree(self.files_dir, ignore_errors=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)

    def _children_sections(self, root: ET.Element) -> dict[str, ET.Element]:
        return {_local(child.tag): child for child in root}

    def _parse_events(self, root: ET.Element) -> dict[str, dict]:
        events: dict[str, dict] = {}
        events_el = self._children_sections(root).get("events")
        if events_el is None:
            return events

        for event in events_el:
            if _local(event.tag) != "event":
                continue
            handle = event.get("handle")
            if not handle:
                continue
            etype = _text(_find(event, "type")) or ""
            date_val = None
            dv = _find(event, "dateval")
            if dv is not None:
                date_val = dv.get("val")
            else:
                ds = _find(event, "datestr")
                if ds is not None:
                    date_val = ds.get("val") or _text(ds)
            place_h = None
            pl = _find(event, "place")
            if pl is not None:
                place_h = pl.get("hlink")
            events[handle] = {"type": etype, "date": date_val, "place": place_h}
        return events

    def _parse_places(self, root: ET.Element) -> dict[str, str]:
        places: dict[str, str] = {}
        places_el = self._children_sections(root).get("places")
        if places_el is None:
            return places
        for place in places_el:
            if _local(place.tag) != "placeobj":
                continue
            handle = place.get("handle")
            title = _text(_find(place, "ptitle"))
            if handle and title:
                places[handle] = title
        return places

    def _resolve_blob(self, src: str, package: PackageContents) -> bytes | None:
        if not src:
            return None
        key = _normalize_path(src)
        if key in package.media_by_path:
            return package.media_by_path[key]
        base = _basename_key(src)
        if base in package.media_by_name:
            return package.media_by_name[base]
        # иногда src — абсолютный путь, basename совпадает
        for path_key, blob in package.media_by_path.items():
            if path_key.endswith("/" + base) or path_key == base:
                return blob
        return None

    def _store_blob(self, blob: bytes, preferred_name: str) -> str:
        """Сохранить файл, вернуть относительный путь files/..."""
        digest = hashlib.sha1(blob, usedforsecurity=False).hexdigest()[:12]
        safe = _safe_filename(preferred_name)
        rel = f"files/{digest}_{safe}"
        dest = self.media_root / rel
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(blob)
        return rel.replace("\\", "/")

    def _parse_objects(
        self,
        root: ET.Element,
        package: PackageContents,
        stats: ImportStats,
    ) -> dict[str, dict]:
        """handle → {src, mime, description, file_path?} для object с файлом в архиве."""
        result: dict[str, dict] = {}
        objects_el = self._children_sections(root).get("objects")
        if objects_el is None:
            return result

        # content hash → relative path (дедуп)
        saved: dict[str, str] = {}

        for obj in objects_el:
            if _local(obj.tag) != "object":
                continue
            handle = obj.get("handle")
            if not handle:
                continue
            f = _find(obj, "file")
            if f is None:
                continue
            src = f.get("src") or ""
            mime = _guess_mime(src, f.get("mime"))
            desc = f.get("description")
            blob = self._resolve_blob(src, package)
            entry: dict = {
                "src": src,
                "mime": mime,
                "description": desc,
                "file_path": None,
            }
            if blob is None:
                stats.media_missing += 1
                result[handle] = entry
                continue

            content_hash = hashlib.sha1(blob, usedforsecurity=False).hexdigest()
            if content_hash in saved:
                rel = saved[content_hash]
            else:
                rel = self._store_blob(blob, Path(src.replace("\\", "/")).name or "photo.jpg")
                saved[content_hash] = rel
                stats.media_imported += 1
            entry["file_path"] = rel
            result[handle] = entry

        return result

    def _import_people(
        self,
        root: ET.Element,
        events: dict[str, dict],
        places: dict[str, str],
        stats: ImportStats,
    ) -> dict[str, Person]:
        handle_map: dict[str, Person] = {}
        people_el = self._children_sections(root).get("people")
        if people_el is None:
            stats.warnings.append("В XML нет секции <people>")
            return handle_map

        for person_el in people_el:
            if _local(person_el.tag) != "person":
                continue
            handle = person_el.get("handle")
            gramps_id = person_el.get("id") or ""
            if not handle:
                stats.skipped_people += 1
                continue

            names = self._extract_names(person_el)
            if not names["first_name"]:
                names["first_name"] = gramps_id or "Без имени"
                stats.warnings.append(
                    f"У {handle} нет имени, подставлено «{names['first_name']}»"
                )

            gender = _map_gender(_text(_find(person_el, "gender")))
            birth_date, death_date, birth_place = self._person_vital_events(
                person_el, events, places
            )

            note_parts = []
            if gramps_id:
                note_parts.append(f"Gramps ID: {gramps_id}")

            def clip(val: str | None, n: int) -> str | None:
                if not val:
                    return None
                return val[:n]

            person = Person(
                first_name=(names["first_name"] or "Без имени")[:100],
                last_name=clip(names["last_name"], 100),
                middle_name=clip(names["middle_name"], 100),
                maiden_name=clip(names["maiden_name"], 100),
                gender=gender,
                birth_date=birth_date,
                death_date=death_date,
                birth_place=clip(birth_place, 255),
                notes=clip("; ".join(note_parts), 2000) if note_parts else None,
            )
            self.db.add(person)
            self.db.flush()
            handle_map[handle] = person
            stats.people += 1

        return handle_map

    def _link_media(
        self,
        root: ET.Element,
        handle_to_person: dict[str, Person],
        object_map: dict[str, dict],
        stats: ImportStats,
    ) -> None:
        people_el = self._children_sections(root).get("people")
        if people_el is None:
            return

        for person_el in people_el:
            if _local(person_el.tag) != "person":
                continue
            phandle = person_el.get("handle")
            person = handle_to_person.get(phandle or "")
            if not person:
                continue

            primary_set = False
            for objref in _findall(person_el, "objref"):
                oh = objref.get("hlink")
                if not oh or oh not in object_map:
                    continue
                meta = object_map[oh]
                if not meta.get("file_path"):
                    continue

                is_primary = not primary_set
                media = Media(
                    person_id=person.id,
                    file_path=meta["file_path"],
                    original_src=(meta.get("src") or None),
                    mime_type=meta.get("mime"),
                    description=(meta.get("description") or None),
                    gramps_handle=oh,
                    is_primary=is_primary,
                )
                self.db.add(media)
                stats.media_linked += 1

                if is_primary:
                    person.photo_path = meta["file_path"]
                    primary_set = True

        self.db.flush()

    def _extract_names(self, person_el: ET.Element) -> dict[str, str | None]:
        birth_first = birth_surname = birth_suffix = None
        married_surname = None
        unknown_first = unknown_surname = unknown_suffix = None
        alt_first = alt_surname = alt_suffix = None

        for name_el in _findall(person_el, "name"):
            ntype = (name_el.get("type") or "").strip().lower()
            first = _text(_find(name_el, "first"))
            surname = _text(_find(name_el, "surname"))
            suffix = _text(_find(name_el, "suffix"))
            nick = _text(_find(name_el, "nick"))

            if ntype in ("birth name", "birth", ""):
                if birth_first is None:
                    birth_first = first or nick
                    birth_surname = surname
                    birth_suffix = suffix
            elif ntype in ("married name", "married"):
                if married_surname is None:
                    married_surname = surname
                    if first and not birth_first:
                        birth_first = first
                    if suffix and not birth_suffix:
                        birth_suffix = suffix
            elif ntype == "unknown":
                # Gramps: основное имя без классификации — текущая фамилия
                if unknown_first is None:
                    unknown_first = first or nick
                    unknown_surname = surname
                    unknown_suffix = suffix
            else:
                if alt_first is None:
                    alt_first = first or nick
                    alt_surname = surname
                    alt_suffix = suffix

        first_name = birth_first or unknown_first or alt_first
        last_name = married_surname or unknown_surname or birth_surname or alt_surname
        middle_name = birth_suffix or unknown_suffix or alt_suffix
        maiden_name = None
        if birth_surname and birth_surname != last_name:
            maiden_name = birth_surname

        return {
            "first_name": first_name,
            "last_name": last_name,
            "middle_name": middle_name,
            "maiden_name": maiden_name,
        }

    def _person_vital_events(
        self,
        person_el: ET.Element,
        events: dict[str, dict],
        places: dict[str, str],
    ) -> tuple[date | None, date | None, str | None]:
        birth_date = death_date = None
        birth_place = None

        for eref in _findall(person_el, "eventref"):
            h = eref.get("hlink")
            if not h or h not in events:
                continue
            ev = events[h]
            etype = (ev["type"] or "").strip().lower()
            if etype in ("birth", "baptism", "christening") and birth_date is None:
                birth_date = parse_gramps_date(ev.get("date"))
                ph = ev.get("place")
                if ph and ph in places:
                    birth_place = places[ph]
            elif etype in ("death", "burial") and death_date is None:
                death_date = parse_gramps_date(ev.get("date"))

        return birth_date, death_date, birth_place

    def _import_families(
        self,
        root: ET.Element,
        handle_map: dict[str, Person],
        stats: ImportStats,
    ) -> None:
        families_el = self._children_sections(root).get("families")
        if families_el is None:
            return

        seen_parent: set[tuple[int, int]] = set()
        seen_spouse: set[tuple[int, int]] = set()

        for fam in families_el:
            if _local(fam.tag) != "family":
                continue
            stats.families += 1

            father_el = _find(fam, "father")
            mother_el = _find(fam, "mother")
            father = handle_map.get(father_el.get("hlink")) if father_el is not None else None
            mother = handle_map.get(mother_el.get("hlink")) if mother_el is not None else None

            if father and mother:
                a, b = sorted([father.id, mother.id])
                key = (a, b)
                if key not in seen_spouse:
                    self.db.add(
                        Relationship(
                            person_id=a,
                            related_person_id=b,
                            relation_type=RelationType.SPOUSE_OF,
                        )
                    )
                    seen_spouse.add(key)
                    stats.relationships += 1

            children: list[Person] = []
            for cref in _findall(fam, "childref"):
                ch = handle_map.get(cref.get("hlink") or "")
                if ch:
                    children.append(ch)

            for parent in (father, mother):
                if not parent:
                    continue
                for child in children:
                    key = (parent.id, child.id)
                    if key in seen_parent:
                        continue
                    self.db.add(
                        Relationship(
                            person_id=parent.id,
                            related_person_id=child.id,
                            relation_type=RelationType.PARENT_OF,
                        )
                    )
                    seen_parent.add(key)
                    stats.relationships += 1
