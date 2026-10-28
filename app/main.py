"""FastAPI приложение для Family Tree Service."""

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.requests import Request

from app import __version__
from app.config import settings
from app.database import get_db, init_db
from app.logging_config import get_access_logger, setup_logging
from app.routers import auth, export_data, import_data, media, persons, relationships, tree
from app.security import (
    SecurityHeadersMiddleware,
    require_docs_auth,
    serve_protected_media,
    serve_protected_media_by_path,
)

setup_logging()

STATIC_DIR = Path(__file__).resolve().parent / "static"
MEDIA_DIR = Path(settings.media_dir)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

access_log = get_access_logger()
logger = logging.getLogger(__name__)


def _migrate_columns() -> None:
    """Лёгкая миграция: добавить недостающие колонки в существующие таблицы."""
    from sqlalchemy import text

    from app.database import engine

    cols = {
        "person_revisions": {"actor": "TEXT"},
        "person_access_links": {"role": "VARCHAR(16) DEFAULT 'editor' NOT NULL"},
    }
    with engine.begin() as conn:
        for table, adds in cols.items():
            existing = {r[1] for r in conn.execute(text(f"PRAGMA table_info({table})"))}
            for col, ddl in adds.items():
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
                    logger.info("Migrated: %s.%s added", table, col)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Starting Family Tree Service v%s", __version__)
    logger.info("Media directory: %s", MEDIA_DIR)
    logger.info("Edit token required: %s", bool(settings.edit_token))
    init_db()
    _migrate_columns()
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    (MEDIA_DIR / "files").mkdir(parents=True, exist_ok=True)
    logger.info("Database initialized")
    logger.info("App startup complete")
    yield
    logger.info("App shutdown")


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description=(
        "REST API для построения семейного дерева: люди, родственные связи, "
        "предки, потомки, путь родства. Импорт Gramps .gpkg с фото."
    ),
    lifespan=lifespan,
    openapi_url=None if settings.protect_api_docs else "/openapi.json",
    docs_url=None if settings.protect_api_docs else "/docs",
    redoc_url=None if settings.protect_api_docs else "/redoc",
)


# ── Middleware ─────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allow_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if settings.enable_security_headers:
    app.add_middleware(SecurityHeadersMiddleware)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.debug("Request: %s %s", request.method, request.url.path)
    start_ts = time.perf_counter()
    response = await call_next(request)
    duration_ms = int((time.perf_counter() - start_ts) * 1000)
    access_log.info(
        "%s %s %s %d",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    logger.debug(
        "Response: %s %s %d %dms",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


# ── Роутеры ───────────────────────────────────────────────────────────────

app.include_router(persons.router, prefix="/api/v1")
app.include_router(relationships.router, prefix="/api/v1")
app.include_router(tree.router, prefix="/api/v1")
app.include_router(import_data.router, prefix="/api/v1")
app.include_router(export_data.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(media.router, prefix="/api/v1")


# ── Защищённая раздача медиа ───────────────────────────────────────────────

@app.get(
    "/media/files/{person_id}/{filename}",
    response_class=FileResponse,
    include_in_schema=False,
)
async def protected_media_file(
    person_id: int,
    filename: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """JWT: Authorization Bearer или httpOnly cookie."""
    from app.security import require_media_auth

    await require_media_auth(request)
    return await serve_protected_media(person_id, filename, db)


@app.get("/media/{file_path:path}", response_class=FileResponse, include_in_schema=False)
async def protected_media_path(
    file_path: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Универсальная защищённая раздача: /media/files/...
    Совместимо с photo_url из моделей.
    """
    return await serve_protected_media_by_path(file_path, request, db)


# ── Статика ────────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
# /media НЕ монтируется как StaticFiles — только защищённые эндпоинты выше


# ── SPA index с поддержкой префикса (деплой за reverse-proxy) ──────────────


def _render_index() -> FileResponse:
    """index.html: инжектит window.ROOT_PATH и чинит абсолютные /static-пути."""
    root = settings.root_path
    if not root:
        return FileResponse(STATIC_DIR / "index.html")
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    # абсолютные ссылки на статику → с префиксом
    html = html.replace('"/static/', f'"{root}/static/')
    # ROOT_PATH для app.js (API, media)
    inject = f'<script>window.ROOT_PATH="{root}";</script>'
    html = html.replace("</head>", f"{inject}</head>", 1)
    return HTMLResponse(html)


# ── Защита OpenAPI ─────────────────────────────────────────────────────────

if settings.protect_api_docs:

    @app.get("/docs", include_in_schema=False)
    @app.get("/openapi.json", include_in_schema=False)
    @app.get("/redoc", include_in_schema=False)
    async def protected_docs(_auth: bool = Depends(require_docs_auth)):
        # При наличии JWT отдаём SPA; Swagger UI при protect_api_docs отключён в конструкторе
        return _render_index()


# ── Системные роуты ────────────────────────────────────────────────────────

@app.get("/")
def index() -> FileResponse:
    return _render_index()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "version": __version__}


logger.info("Registered routes:")
for route in app.routes:
    if hasattr(route, "path") and hasattr(route, "methods"):
        logger.info("  %s %s", route.methods, route.path)
