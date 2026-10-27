from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.routers.auth import require_editor_token
from app.services.obsidian_export import ObsidianExporter

router = APIRouter(prefix="/export", tags=["export"])


class ObsidianExportResult(BaseModel):
    people: int
    files: int
    photos: int
    export_dir: str
    warnings: list[str] = Field(default_factory=list)
    message: str


@router.post("/obsidian", response_model=ObsidianExportResult, dependencies=[Depends(require_editor_token)])
def export_obsidian(
    clean: bool = Query(True, description="Очистить папку экспорта перед записью"),
    db: Session = Depends(get_db),
) -> ObsidianExportResult:
    """Экспорт всех людей в Markdown для Obsidian (frontmatter + wiki-ссылки)."""
    exporter = ObsidianExporter(db)
    try:
        stats = exporter.export(clean=clean)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Ошибка экспорта: {exc}",
        ) from exc

    return ObsidianExportResult(
        people=stats.people,
        files=stats.files,
        photos=stats.photos,
        export_dir=stats.export_dir,
        warnings=stats.warnings,
        message=(
            f"Экспортировано {stats.people} чел. в {stats.export_dir} "
            f"({stats.files} файлов, {stats.photos} фото)"
        ),
    )


@router.get("/obsidian/zip", dependencies=[Depends(require_editor_token)])
def export_obsidian_zip(db: Session = Depends(get_db)) -> FileResponse:
    """Собрать vault и отдать ZIP для скачивания."""
    exporter = ObsidianExporter(db)
    try:
        zip_path, stats = exporter.export_zip()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Ошибка экспорта: {exc}",
        ) from exc

    if not stats.people:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет людей для экспорта")

    return FileResponse(
        path=zip_path,
        filename="family_tree_obsidian.zip",
        media_type="application/zip",
    )


@router.get("/obsidian/path", dependencies=[Depends(require_editor_token)])
def export_obsidian_path() -> dict[str, str]:
    """Путь к папке экспорта по умолчанию."""
    return {"export_dir": str(Path(settings.obsidian_export_dir).resolve())}
