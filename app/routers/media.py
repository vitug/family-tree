from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session
import logging

from app.routers.auth import require_jwt_token, require_editor_token
from app.config import settings
from app.database import get_db
from app.models import Media, Person
from app.schemas import MediaRead
from app.security import unlink_media_file

router = APIRouter(prefix="/persons", tags=["media"])
logger = logging.getLogger(__name__)


def get_person(person_id: int, db: Session = Depends(get_db)) -> Person:
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Человек id={person_id} не найден")
    return person


SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/tiff", "image/bmp"}
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB


def _validate_image_meta(file: UploadFile) -> None:
    """Быстрая проверка заявленного content-type (сами байты — в _sniff_image_type)."""
    if not file.content_type or file.content_type not in SUPPORTED_IMAGE_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Тип {file.content_type} не поддерживается. Допустимые: {', '.join(SUPPORTED_IMAGE_TYPES)}",
        )


def _sniff_image_type(content: bytes) -> str | None:
    """Определить формат по магическим байтам — не верим content-type от клиента."""
    if content[:3] == b"\xFF\xD8\xFF":
        return "image/jpeg"
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if content[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if content[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if content[:2] == b"BM":
        return "image/bmp"
    return None


def _validate_image_bytes(content: bytes) -> None:
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пустой файл")
    if len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Файл слишком большой (макс. {MAX_IMAGE_SIZE // 1024 // 1024} МБ)",
        )


def _person_photo_dir(person_id: int) -> Path:
    base = Path(settings.media_dir) / "files"
    pdir = base / str(person_id)
    pdir.mkdir(parents=True, exist_ok=True)
    return pdir


THUMB_SIZE = 256


def _make_thumbnail(dest: Path) -> Path | None:
    """Миниатюра t_<имя>.jpg рядом с фото: списки/карточки тянут ~15 КБ
    вместо полных ~500 КБ; полное фото — только в карточке человека.
    При отсутствии Pillow тихо пропускаем (фронт падает на полный URL).
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    thumb = dest.parent / f"t_{dest.stem}.jpg"
    try:
        with Image.open(dest) as im:
            im.thumbnail((THUMB_SIZE, THUMB_SIZE))
            im.convert("RGB").save(thumb, "JPEG", quality=80, optimize=True)
        return thumb
    except Exception:
        logger.warning("thumbnail failed for %s", dest, exc_info=True)
        return None


@router.post("/{person_id}/photo", response_model=MediaRead, status_code=201, dependencies=[Depends(require_editor_token)])
async def upload_person_photo(
    person: Annotated[Person, Depends(get_person)],
    is_primary: bool = Query(True, description="Сделать этот фото основным"),
    file: UploadFile = File(..., description="Изображение до 10 МБ"),
    db: Session = Depends(get_db),
) -> Media:
    """Загрузить фото для человека."""
    _validate_image_meta(file)
    content = await file.read()
    _validate_image_bytes(content)

    # Расширение и MIME — по магическим байтам, а не по заявлению клиента
    sniffed = _sniff_image_type(content)
    if sniffed is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Файл не является корректным изображением (проверка магических байтов не прошла)",
        )
    logger.info(
        "Upload photo for person_id=%d size=%d bytes sniffed=%s declared=%s",
        person.id,
        len(content),
        sniffed,
        file.content_type,
    )

    ext_map = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/tiff": ".tif",
        "image/bmp": ".bmp",
    }
    ext = ext_map[sniffed]

    from hashlib import sha1

    h = sha1(content, usedforsecurity=False).hexdigest()[:12]
    fname = f"{h}{ext}"
    dest = _person_photo_dir(person.id) / fname
    dest.write_bytes(content)
    _make_thumbnail(dest)

    if is_primary:
        db.query(Media).filter(Media.person_id == person.id, Media.is_primary).update({Media.is_primary: False})
        person.photo_path = f"files/{person.id}/{fname}"

    media = Media(
        person_id=person.id,
        file_path=f"files/{person.id}/{fname}",
        original_src=file.filename,
        mime_type=sniffed,
        description=None,
        is_primary=is_primary,
    )
    db.add(media)
    db.commit()
    db.refresh(media)
    return media


@router.delete("/{person_id}/photo/all", status_code=204, dependencies=[Depends(require_editor_token)])
async def delete_person_all_photos(
    person: Annotated[Person, Depends(get_person)],
    db: Session = Depends(get_db),
) -> None:
    """Удалить все фото человека (записи БД и файлы на диске)."""
    media_rows = list(
        db.scalars(select(Media).where(Media.person_id == person.id)).all()
    )
    paths = [m.file_path for m in media_rows]
    db.query(Media).filter(Media.person_id == person.id).delete()
    person.photo_path = None
    db.commit()
    for path in paths:
        unlink_media_file(path)


@router.delete("/{person_id}/photo/{media_id}", status_code=204, dependencies=[Depends(require_editor_token)])
async def delete_person_photo(
    person: Annotated[Person, Depends(get_person)],
    media_id: int,
    db: Session = Depends(get_db),
) -> None:
    """Удалить конкретное фото (запись и файл)."""
    m = db.get(Media, media_id)
    if not m or m.person_id != person.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Фото id={media_id} не найдено")
    file_path = m.file_path
    if m.file_path == person.photo_path:
        person.photo_path = None
    db.delete(m)
    db.commit()
    unlink_media_file(file_path)


@router.get("/{person_id}/photos", response_model=list[MediaRead], dependencies=[Depends(require_jwt_token)])
async def list_person_photos(
    person: Annotated[Person, Depends(get_person)],
    db: Session = Depends(get_db),
) -> list[Media]:
    return list(
        db.scalars(
            select(Media).where(Media.person_id == person.id).order_by(Media.is_primary.desc(), Media.id)
        ).all()
    )
