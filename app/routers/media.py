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

router = APIRouter(prefix="/persons", tags=["media"])
logger = logging.getLogger(__name__)


def get_person(person_id: int, db: Session = Depends(get_db)) -> Person:
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Человек id={person_id} не найден")
    return person


SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/tiff", "image/bmp"}
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB


def _validate_image(file: UploadFile) -> None:
    if file.size and file.size > MAX_IMAGE_SIZE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Файл слишком большой (макс. {MAX_IMAGE_SIZE // 1024 // 1024} МБ)")
    if not file.content_type or file.content_type not in SUPPORTED_IMAGE_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Тип {file.content_type} не поддерживается. Допустимые: {', '.join(SUPPORTED_IMAGE_TYPES)}",
        )


def _person_photo_dir(person_id: int) -> Path:
    base = Path(settings.media_dir) / "files"
    pdir = base / str(person_id)
    pdir.mkdir(parents=True, exist_ok=True)
    return pdir


@router.post("/{person_id}/photo", response_model=MediaRead, status_code=201, dependencies=[Depends(require_editor_token)])
async def upload_person_photo(
    person: Annotated[Person, Depends(get_person)],
    is_primary: bool = Query(True, description="Сделать этот фото основным"),
    file: UploadFile = File(..., description="Изображение до 10 МБ"),
    db: Session = Depends(get_db),
) -> Media:
    """Загрузить фото для человека."""
    _validate_image(file)
    content = await file.read()
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пустой файл")

    logger.info("Upload photo for person_id=%d size=%d bytes", person.id, len(content))
    ext_map = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/tiff": ".tif",
        "image/bmp": ".bmp",
    }
    ext = ext_map.get(file.content_type, ".jpg")

    # uniq filename
    from hashlib import sha1

    h = sha1(content).hexdigest()[:12]
    fname = f"{h}{ext}"
    dest = _person_photo_dir(person.id) / fname
    dest.write_bytes(content)

    # если надо, обновить primary
    if is_primary:
        db.query(Media).filter(Media.person_id == person.id, Media.is_primary).update({Media.is_primary: False})
        person.photo_path = f"files/{person.id}/{fname}"

    media = Media(
        person_id=person.id,
        file_path=f"files/{person.id}/{fname}",
        original_src=file.filename,
        mime_type=file.content_type,
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
    """Удалить все фото человека."""
    db.query(Media).filter(Media.person_id == person.id).delete()
    person.photo_path = None
    db.commit()
    # TODO: очистить файлы на диске асинхронно


@router.delete("/{person_id}/photo/{media_id}", status_code=204, dependencies=[Depends(require_editor_token)])
async def delete_person_photo(
    person: Annotated[Person, Depends(get_person)],
    media_id: int,
    db: Session = Depends(get_db),
) -> None:
    """Удалить конкретное фото."""
    m = db.get(Media, media_id)
    if not m or m.person_id != person.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Фото id={media_id} не найдено")
    if m.file_path == person.photo_path:
        person.photo_path = None
    db.delete(m)
    db.commit()


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
