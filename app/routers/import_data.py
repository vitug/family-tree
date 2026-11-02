import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.routers.auth import require_editor_token
from app.database import get_db
from app.services.gramps_import import GrampsImporter

router = APIRouter(prefix="/import", tags=["import"])

_MAX_SIZE = 50 * 1024 * 1024


async def _read_upload(file: UploadFile, exts: tuple[str, ...]) -> tuple[bytes, str]:
    filename = file.filename or "upload"
    if not filename.lower().endswith(exts):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Поддерживаются файлы {', '.join(exts)}",
        )
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пустой файл")
    if len(data) > _MAX_SIZE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файл слишком большой (макс. 50 МБ)")
    return data, filename


class _Candidate(BaseModel):
    person_id: int
    full_name: str
    birth_date: str | None = None
    reason: str
    detail: str


class _PreviewPerson(BaseModel):
    key: str
    name: str
    first_name: str
    last_name: str | None = None
    middle_name: str | None = None
    gender: str = "unknown"
    birth_date: str | None = None
    death_date: str | None = None
    birth_place: str | None = None
    candidates: list[_Candidate] = Field(default_factory=list)


class ImportPreview(BaseModel):
    source: str
    people: list[_PreviewPerson]
    total: int
    matched: int
    message: str


def _d(v) -> str | None:
    return v.isoformat() if v else None


def _preview_payload(source: str, parsed_people, matcher_results) -> ImportPreview:
    people = []
    matched = 0
    for person, candidates in matcher_results:
        if candidates:
            matched += 1
        people.append(
            _PreviewPerson(
                key=person.key,
                name=person.display_name,
                first_name=person.first_name,
                last_name=person.last_name,
                middle_name=person.middle_name,
                gender=person.gender,
                birth_date=_d(person.birth_date),
                death_date=_d(person.death_date),
                birth_place=person.birth_place,
                candidates=[
                    _Candidate(
                        person_id=c.person_id,
                        full_name=c.full_name,
                        birth_date=_d(c.birth_date),
                        reason=c.reason,
                        detail=c.detail,
                    )
                    for c in candidates
                ],
            )
        )
    return ImportPreview(
        source=source,
        people=people,
        total=len(people),
        matched=matched,
        message=f"В файле {len(people)} чел., авто-совпадений: {matched}. Проверьте связи перед импортом.",
    )


def _parse_decisions(raw: str | None) -> dict[str, int | None]:
    """JSON вида {"I0004": 17, "I0005": null} → dict для импортёров."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Некорректный JSON решений: {exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Решения должны быть JSON-объектом {ключ: id|null}")
    out: dict[str, int | None] = {}
    for key, value in data.items():
        if value is None:
            out[str(key)] = None
        else:
            try:
                out[str(key)] = int(value)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, f"Некорректный id человека для {key}: {value}"
                ) from exc
    return out


class GrampsImportResult(BaseModel):
    people: int
    families: int
    relationships: int
    skipped_people: int = 0
    updated_people: int = 0
    linked_people: int = 0
    media_files: int = 0
    media_imported: int = 0
    media_linked: int = 0
    media_missing: int = 0
    warnings: list[str] = Field(default_factory=list)
    message: str


@router.post("/gramps", response_model=GrampsImportResult, dependencies=[Depends(require_editor_token)])
async def import_gramps(
    file: UploadFile = File(..., description="Файл .gpkg или .gramps"),
    replace: bool = Query(
        False,
        description="Если true — очистить дерево перед импортом",
    ),
    decisions: str = Form(default="", description='JSON: {"I0004": 17, "I0005": null}'),
    db: Session = Depends(get_db),
) -> GrampsImportResult:
    """Импорт из Gramps XML Package (.gpkg) или Gramps XML (.gramps)."""
    data, filename = await _read_upload(file, (".gpkg", ".gramps", ".xml"))
    parsed_decisions = _parse_decisions(decisions)

    importer = GrampsImporter(db)
    try:
        stats = importer.import_bytes(
            data,
            filename=filename,
            replace=replace,
            decisions=parsed_decisions,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Ошибка импорта: {exc}",
        ) from exc

    parts = [
        f"{stats.people} чел.",
        f"{stats.families} семей",
        f"{stats.relationships} связей",
    ]
    if stats.linked_people:
        parts.append(f"{stats.linked_people} склеено")
    if stats.media_imported:
        parts.append(f"{stats.media_imported} фото")
    if stats.media_linked:
        parts.append(f"{stats.media_linked} привязок фото")

    return GrampsImportResult(
        people=stats.people,
        families=stats.families,
        relationships=stats.relationships,
        skipped_people=stats.skipped_people,
        updated_people=stats.updated_people,
        linked_people=stats.linked_people,
        media_files=stats.media_files,
        media_imported=stats.media_imported,
        media_linked=stats.media_linked,
        media_missing=stats.media_missing,
        warnings=stats.warnings,
        message="Импортировано: " + ", ".join(parts),
    )


class GedcomImportResult(BaseModel):
    people: int
    families: int
    relationships: int
    updated_people: int = 0
    linked_people: int = 0
    skipped_people: int = 0
    warnings: list[str] = Field(default_factory=list)
    message: str


@router.post("/gedcom", response_model=GedcomImportResult, dependencies=[Depends(require_editor_token)])
async def import_gedcom(
    file: UploadFile = File(..., description="Файл .ged (GEDCOM 5.5.1)"),
    replace: bool = Query(
        False,
        description="Если true — очистить дерево перед импортом",
    ),
    decisions: str = Form(default="", description='JSON: {"@I12@": 17, "@I13@": null}'),
    db: Session = Depends(get_db),
) -> GedcomImportResult:
    """Импорт из GEDCOM 5.5.1 (.ged): люди, семьи, даты. Xref сохраняется."""
    data, filename = await _read_upload(file, (".ged",))
    parsed_decisions = _parse_decisions(decisions)

    from app.services.gedcom_import import GedcomImporter

    importer = GedcomImporter(db)
    try:
        stats = importer.import_bytes(
            data, replace=replace, decisions=parsed_decisions
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Ошибка импорта: {exc}") from exc

    parts = [f"{stats.people} чел.", f"{stats.families} семей", f"{stats.relationships} связей"]
    if stats.updated_people:
        parts.append(f"{stats.updated_people} обновлено")
    if stats.linked_people:
        parts.append(f"{stats.linked_people} склеено")
    return GedcomImportResult(
        people=stats.people,
        families=stats.families,
        relationships=stats.relationships,
        updated_people=stats.updated_people,
        linked_people=stats.linked_people,
        skipped_people=stats.skipped_people,
        warnings=stats.warnings,
        message="Импортировано: " + ", ".join(parts),
    )


# ── preview: разбор файла без записи, с кандидатами на склейку ─────────────


@router.post(
    "/gramps/preview",
    response_model=ImportPreview,
    dependencies=[Depends(require_editor_token)],
)
async def preview_gramps(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> ImportPreview:
    """Разбор Gramps-файла без записи в базу + кандидаты на склейку."""
    from app.services.import_matcher import find_candidates

    data, filename = await _read_upload(file, (".gpkg", ".gramps", ".xml"))
    importer = GrampsImporter(db)
    try:
        parsed_people = importer.preview(data, filename=filename)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return _preview_payload(
        "gramps", parsed_people, [(p, find_candidates(db, p, "gramps")) for p in parsed_people]
    )


@router.post(
    "/gedcom/preview",
    response_model=ImportPreview,
    dependencies=[Depends(require_editor_token)],
)
async def preview_gedcom(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> ImportPreview:
    """Разбор GEDCOM-файла без записи в базу + кандидаты на склейку."""
    from app.services.gedcom_import import GedcomImporter
    from app.services.import_matcher import find_candidates

    data, _ = await _read_upload(file, (".ged",))
    importer = GedcomImporter(db)
    try:
        parsed = importer.parse_bytes(data)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    people = parsed.people
    return _preview_payload(
        "gedcom", people, [(p, find_candidates(db, p, "gedcom")) for p in people]
    )
