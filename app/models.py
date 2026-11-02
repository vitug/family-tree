from __future__ import annotations

import enum
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import Date, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.database import Base


class Gender(str, enum.Enum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"
    UNKNOWN = "unknown"


class RelationType(str, enum.Enum):
    """Типы прямых связей между людьми.

    PARENT_OF: person_id — родитель, related_person_id — ребёнок.
    SPOUSE_OF: супруги (симметричная связь хранится в одном направлении + reverse при чтении).
    """

    PARENT_OF = "parent_of"
    SPOUSE_OF = "spouse_of"


class Person(Base):
    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(100))
    middle_name: Mapped[str | None] = mapped_column(String(100))
    maiden_name: Mapped[str | None] = mapped_column(String(100))
    gender: Mapped[Gender] = mapped_column(Enum(Gender), default=Gender.UNKNOWN)
    birth_date: Mapped[date | None] = mapped_column(Date)
    death_date: Mapped[date | None] = mapped_column(Date)
    birth_place: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    # Внешний идентификатор из источника импорта (Gramps ID, GEDCOM xref @I12@)
    external_id: Mapped[str | None] = mapped_column(String(64))
    external_source: Mapped[str | None] = mapped_column(String(32))
    # Относительный путь внутри media_dir, например "files/abc_photo.jpg"
    photo_path: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    relations_from: Mapped[list[Relationship]] = relationship(
        "Relationship",
        foreign_keys="Relationship.person_id",
        back_populates="person",
        cascade="all, delete-orphan",
    )
    relations_to: Mapped[list[Relationship]] = relationship(
        "Relationship",
        foreign_keys="Relationship.related_person_id",
        back_populates="related_person",
        cascade="all, delete-orphan",
    )
    media: Mapped[list[Media]] = relationship(
        "Media",
        back_populates="person",
        cascade="all, delete-orphan",
        order_by="Media.id",
    )

    @property
    def full_name(self) -> str:
        parts = [self.last_name, self.first_name, self.middle_name]
        return " ".join(p for p in parts if p)

    @property
    def photo_url(self) -> str | None:
        """URL для защищённого эндпоинта /media/... (нужен JWT Bearer или cookie)."""
        if self.photo_path:
            rel = self.photo_path.replace(chr(92), "/").lstrip("/")
            return f"/media/{rel}"
        return None

    @property
    def photo_thumb_url(self) -> str | None:
        """Миниатюра (t_<имя>.jpg) для списков/карточек; None → фронт берёт полный URL."""
        if not self.photo_path:
            return None
        p = Path(self.photo_path)
        rel = (p.parent / f"t_{p.stem}.jpg").as_posix().lstrip("/")
        if (Path(settings.media_dir) / rel).is_file():
            return f"/media/{rel}"
        return None


class Relationship(Base):
    __tablename__ = "relationships"
    __table_args__ = (
        UniqueConstraint(
            "person_id",
            "related_person_id",
            "relation_type",
            name="uq_relationship",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id", ondelete="CASCADE"), nullable=False)
    related_person_id: Mapped[int] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"), nullable=False
    )
    relation_type: Mapped[RelationType] = mapped_column(Enum(RelationType), nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    person: Mapped[Person] = relationship(
        "Person", foreign_keys=[person_id], back_populates="relations_from"
    )
    related_person: Mapped[Person] = relationship(
        "Person", foreign_keys=[related_person_id], back_populates="relations_to"
    )


class PersonAccessLink(Base):
    """Персональная ссылка доступа: открывает дерево конкретного человека."""

    __tablename__ = "person_access_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    person_id: Mapped[int] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(default=True)
    # роль сессии по этой ссылке: editor (может править) или viewer
    role: Mapped[str] = mapped_column(String(16), default="editor")
    # Ограничение прав: глубина семьи в поколениях (NULL = без ограничений)
    scope_depth: Mapped[int | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    person: Mapped["Person"] = relationship("Person")


class NoteAccessRequest(Base):
    """Запрос viewer'а на доступ к полным заметкам конкретного человека.

    Админ подтверждает — после этого заметки этого человека не маскируются
    для сессий, пришедших по персональной ссылке того же человека.
    """

    __tablename__ = "note_access_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    # о ком запрос
    person_id: Mapped[int] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # кто просит: person_id персональной ссылки (если есть) + имя
    requester_person_id: Mapped[int | None] = mapped_column(
        ForeignKey("persons.id", ondelete="SET NULL"), nullable=True, index=True
    )
    requester_name: Mapped[str | None] = mapped_column(String(400))
    message: Mapped[str | None] = mapped_column(String(1000))
    # pending | approved | denied
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    person: Mapped["Person"] = relationship("Person", foreign_keys=[person_id])
    requester: Mapped["Person | None"] = relationship(
        "Person", foreign_keys=[requester_person_id]
    )


class Media(Base):
    """Фото/медиа, импортированные из Gramps или загруженные вручную."""

    __tablename__ = "media"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int | None] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Относительный путь в media_dir: "files/....jpg"
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    original_src: Mapped[str | None] = mapped_column(String(512))
    mime_type: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(255))
    gramps_handle: Mapped[str | None] = mapped_column(String(64), index=True)
    is_primary: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    person: Mapped[Person | None] = relationship("Person", back_populates="media")

    @property
    def url(self) -> str:
        """URL для защищённого эндпоинта /media/... (нужен JWT Bearer или cookie)."""
        rel = self.file_path.replace(chr(92), "/").lstrip("/")
        return f"/media/{rel}"


class PersonRevision(Base):
    """История правок человека: create / update / delete с diff полей.

    Хранится отдельно от persons (без FK): ревизии переживают удаление
    человека — в person_name остаётся снимок имени на момент правки.
    """

    __tablename__ = "person_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(nullable=False, index=True)
    person_name: Mapped[str | None] = mapped_column(String(400))
    action: Mapped[str] = mapped_column(String(16))  # create | update | delete
    # кто сделал правку: имя человека по персональной ссылке или «Редактор»
    actor: Mapped[str | None] = mapped_column(String(400))
    # JSON-массив [{field, old, new}] — только реально изменившиеся поля
    changes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
