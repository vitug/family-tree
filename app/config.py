import logging
import secrets
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
    
    # JWT настройки
    jwt_secret_key: str = secrets.token_urlsafe(32)  # Автоматически генерируется
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_hours: int = 24
    
    # Токены авторизации для разных уровней доступа
    view_token: str = "family"      # Для чтения данных (viewer роль)
    edit_token: str = "secret"      # Для чтения и записи (editor роль)
    
    log_level: str = "INFO"


settings = Settings()

# Настроить базовое логирование
logging.basicConfig(
    level=settings.log_level.upper(),
    format="[%(asctime)s] %(levelname)-8s [%(process)d] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
