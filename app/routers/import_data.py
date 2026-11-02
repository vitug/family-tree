from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.routers.auth import require_editor_token
from app.database import get_db
from app.services.gramps_import import GrampsImporter

router = APIRouter(prefix="/import", tags=["import"])


class GrampsImportResult(BaseModel):
    people: int
    families: int
    relationships: int
    skipped_people: int = 0
    updated_people: int = 0
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
    db: Session = Depends(get_db),
) -> GrampsImportResult:
    """Импорт из Gramps XML Package (.gpkg) или Gramps XML (.gramps)."""
    filename = file.filename or "upload.gpkg"
    lower = filename.lower()
    if not (lower.endswith(".gpkg") or lower.endswith(".gramps") or lower.endswith(".xml")):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Поддерживаются файлы .gpkg, .gramps и .xml",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пустой файл")

    # разумный лимит ~50 МБ
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файл слишком большой (макс. 50 МБ)")

    importer = GrampsImporter(db)
    try:
        stats = importer.import_bytes(data, filename=filename, replace=replace)
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
    db: Session = Depends(get_db),
) -> GedcomImportResult:
    """Импорт из GEDCOM 5.5.1 (.ged): люди, семьи, даты. Xref сохраняется."""
    filename = file.filename or "upload.ged"
    if not filename.lower().endswith(".ged"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Поддерживаются файлы .ged")

    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пустой файл")

    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файл слишком большой (макс. 50 МБ)")

    from app.services.gedcom_import import GedcomImporter

    importer = GedcomImporter(db)
    try:
        stats = importer.import_bytes(data, replace=replace)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Ошибка импорта: {exc}") from exc

    parts = [f"{stats.people} чел.", f"{stats.families} семей", f"{stats.relationships} связей"]
    if stats.updated_people:
        parts.append(f"{stats.updated_people} обновлено")
    return GedcomImportResult(
        people=stats.people,
        families=stats.families,
        relationships=stats.relationships,
        updated_people=stats.updated_people,
        skipped_people=stats.skipped_people,
        warnings=stats.warnings,
        message="Импортировано: " + ", ".join(parts),
    )
