from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

def cyrillic_lower(text: str | None) -> str | None:
    """Функция для корректного перевода кириллицы в нижний регистр для SQLite."""
    if text is None:
        return None
    return text.lower()

def cyrillic_upper(text: str | None) -> str | None:
    """Функция для корректного перевода кириллицы в верхний регистр для SQLite."""
    if text is None:
        return None
    return text.upper()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)

# Добавляем пользовательские функции для работы с кириллицей в SQLite
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Регистрирует пользовательские функции для кириллицы при каждом connect."""
    if not settings.database_url.startswith("sqlite"):
        return
    # Имена в нижнем регистре — так их вызывает SQLAlchemy func.cyrillic_lower
    dbapi_connection.create_function("cyrillic_lower", 1, cyrillic_lower)
    dbapi_connection.create_function("cyrillic_upper", 1, cyrillic_upper)
    dbapi_connection.create_function("CYRILLIC_LOWER", 1, cyrillic_lower)
    dbapi_connection.create_function("CYRILLIC_UPPER", 1, cyrillic_upper)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _migrate_sqlite() -> None:
    """Добавить новые колонки в существующую SQLite-базу."""
    if not settings.database_url.startswith("sqlite"):
        return
    with engine.begin() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        }
        if "persons" not in tables:
            return
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(persons)")).fetchall()}
        if "photo_path" not in cols:
            conn.execute(text("ALTER TABLE persons ADD COLUMN photo_path VARCHAR(512)"))


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate_sqlite()
    Path(settings.media_dir).mkdir(parents=True, exist_ok=True)
    (Path(settings.media_dir) / "files").mkdir(parents=True, exist_ok=True)
