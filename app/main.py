from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import settings
from app.database import init_db
from app.routers import export_data, import_data, media, persons, relationships, tree

STATIC_DIR = Path(__file__).resolve().parent / "static"
MEDIA_DIR = Path(settings.media_dir)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description=(
        "REST API для построения семейного дерева: люди, родственные связи, "
        "предки, потомки, путь родства. Импорт Gramps .gpkg с фото."
    ),
    lifespan=lifespan,
)

app.include_router(persons.router, prefix="/api/v1")
app.include_router(relationships.router, prefix="/api/v1")
app.include_router(tree.router, prefix="/api/v1")
app.include_router(import_data.router, prefix="/api/v1")
app.include_router(export_data.router, prefix="/api/v1")
app.include_router(media.router, prefix="/api/v1")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "version": __version__}
