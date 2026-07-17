"""FastAPI приложение для Family Tree Service."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from app import __version__
from app.config import settings
from app.database import init_db
from app.logging_config import get_access_logger, setup_logging
from app.routers import auth, export_data, import_data, media, persons, relationships, tree
from app.services.tree_service import TreeService

# Настроить логирование
setup_logging()

STATIC_DIR = Path(__file__).resolve().parent / "static"
MEDIA_DIR = Path(settings.media_dir)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

access_log = get_access_logger()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan контекстный менеджер для инициализации приложения."""
    logger.info("Starting Family Tree Service v%s", __version__)
    logger.info("Media directory: %s", MEDIA_DIR)
    logger.info("Edit token required: %s", bool(settings.edit_token))
    init_db()
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
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
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Middleware для расширенного логирования запросов в файл logs/access.log."""
    logger.debug("Request: %s %s", request.method, request.url.path)
    import time

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
    logger.debug("Response: %s %s %d %dms", request.method, request.url.path, response.status_code, duration_ms)
    return response


# Регистрация роутеров
app.include_router(persons.router, prefix="/api/v1")
app.include_router(relationships.router, prefix="/api/v1")
app.include_router(tree.router, prefix="/api/v1")
app.include_router(import_data.router, prefix="/api/v1")
app.include_router(export_data.router, prefix="/api/v1")
app.include_router(media.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")

# Логируем зарегистрированные маршруты
logger.info("Registered routes:")
for route in app.routes:
    if hasattr(route, "path") and hasattr(route, "methods"):
        logger.info("  %s %s", route.methods, route.path)

# Статические файлы
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


@app.get("/")
def index() -> FileResponse:
    logger.debug("Serving index.html")
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    logger.debug("Health check")
    return {"status": "ok", "service": settings.app_name, "version": __version__}
