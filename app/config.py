import logging
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Корень проекта (рядом с family_tree.db)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FAMILY_TREE_")

    app_name: str = "Family Tree Service"
    database_url: str = "sqlite:///./family_tree.db"
    max_tree_depth: int = 20
    media_dir: str = str(PROJECT_ROOT / "media")
    obsidian_export_dir: str = str(PROJECT_ROOT / "export" / "obsidian")
    edit_token: str = "secret"
    log_level: str = "INFO"


settings = Settings()

# Настроить базовое логирование
logging.basicConfig(
    level=settings.log_level.upper(),
    format="[%(asctime)s] %(levelname)-8s [%(process)d] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
