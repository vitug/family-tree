from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
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
