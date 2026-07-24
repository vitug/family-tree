import logging
import secrets
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator

# Корень проекта (рядом с family_tree.db)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FAMILY_TREE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Family Tree Service"
    database_url: str = "sqlite:///./family_tree.db"
    max_tree_depth: int = 20
    media_dir: str = str(PROJECT_ROOT / "media")
    obsidian_export_dir: str = str(PROJECT_ROOT / "export" / "obsidian")

    # JWT настройки
    jwt_secret_key: str = secrets.token_urlsafe(32)  # Генерируется автоматически, но лучше задать через env
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_hours: int = 24

    # Токены авторизации для разных уровней доступа
    view_token: str = "family-view-token-dev"  # Для чтения данных (viewer роль)
    edit_token: str = "secret-edit-token-dev"  # Для чтения и записи (editor роль)

    # Безопасность
    enable_rate_limiting: bool = True  # Включить rate limiting
    enable_brute_force_protection: bool = True  # Включить защиту от перебора
    enable_security_headers: bool = True  # Добавить security headers
    protect_api_docs: bool = True  # Требовать JWT для /docs и /openapi.json
    allow_cors_origins: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000"]  # CORS origins

    log_level: str = "INFO"

    @field_validator("jwt_secret_key")
    @classmethod
    def jwt_secret_key_must_be_strong(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("JWT_SECRET_KEY должен быть не менее 32 символов")
        return v

    @field_validator("view_token", "edit_token")
    @classmethod
    def tokens_must_be_strong(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 12:
            raise ValueError(f"Токен {v!r} слишком короткий (минимум 12 символов)")
        # Разрешаем dev токены только если они явно заданы в коде
        if v in ["family-view-token-dev", "secret-edit-token-dev"]:
            logging.warning(
                "Использован стандартный dev-токен (%s). Для продакшена установите .env!",
                v,
            )
        return v


settings = Settings()

# Настроить базовое логирование
logging.basicConfig(
    level=settings.log_level.upper(),
    format="[%(asctime)s] %(levelname)-8s [%(process)d] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
