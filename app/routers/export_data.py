from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.routers.auth import require_editor_token
from app.services.gramps_export import GrampsExporter
from app.services.obsidian_export import ObsidianExporter
from app.services.wiki_export import WikiExporter

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


@router.get("/wiki", dependencies=[Depends(require_editor_token)])
def export_wiki(db: Session = Depends(get_db)) -> Response:
    """Экспорт дерева в формат Karpathy LLM-wiki (zip: SCHEMA, index, log, entities/)."""
    exporter = WikiExporter(db)
    try:
        zip_bytes, stats = exporter.export_zip()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Ошибка экспорта: {exc}") from exc
    if not stats.people:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет людей для экспорта")
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="family_tree_wiki.zip"'},
    )


@router.get("/gramps", dependencies=[Depends(require_editor_token)])
def export_gramps(db: Session = Depends(get_db)) -> Response:
    """Экспорт дерева в Gramps (.gpkg): XML + фото, импортируется в Gramps напрямую."""
    exporter = GrampsExporter(db)
    try:
        pkg_bytes, stats = exporter.export_gpkg()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Ошибка экспорта: {exc}") from exc
    if not stats.people:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет людей для экспорта")
    return Response(
        content=pkg_bytes,
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="family_tree.gpkg"'},
    )
