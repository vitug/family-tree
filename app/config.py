import logging
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

    # JWT-секрет задаётся ТОЛЬКО через .env (FAMILY_TREE_JWT_SECRET_KEY).
    # Пустой дефолт + обязательная валидация при старте: раньше случайный
    # секрет на каждом рестарте молча инвалидировал все JWT/cookie.
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_hours: int = 24

    # Приоритетная семья: живые носители фамилии и их ближайшие
    # родственники (супруги, дети) показываются первыми в списке/обзоре.
    priority_family: str = ""

    # Привязка основной viewer-ссылки к человеку: после входа открывает
    # его карточку (id из persons; 0 = без привязки).
    view_focus_person_id: int = 0

    # Токены авторизации для разных уровней доступа
    view_token: str = "family-view-token-dev"  # Для чтения данных (viewer роль)
    edit_token: str = "secret-edit-token-dev"  # Для чтения и записи (editor роль)

    # Доверять X-Forwarded-For / X-Real-IP только за явным opt-in
    # (когда сервис реально стоит за reverse-proxy). Иначе клиент может
    # подменять заголовки и обходить rate limit / brute-force lockout.
    trust_proxy: bool = False

    # Безопасность
    enable_rate_limiting: bool = True  # Включить rate limiting
    enable_brute_force_protection: bool = True  # Включить защиту от перебора
    enable_security_headers: bool = True  # Добавить security headers
    protect_api_docs: bool = True  # Требовать JWT для /docs и /openapi.json
    allow_cors_origins: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000"]  # CORS origins

    log_level: str = "INFO"

    # Префикс пути при деплое за reverse-proxy (например /fTk9abcX на vitug.ru).
    # Пустой = корень. Приложение само подставляет его в SPA и редиректы,
    # nginx при этом проксирует БЕЗ URI-части (полный путь уходит целиком).
    root_path: str = ""

    @field_validator("root_path")
    @classmethod
    def root_path_must_be_slash_prefixed(cls, v: str) -> str:
        v = v.strip()
        if not v:
            return ""
        if not v.startswith("/"):
            v = "/" + v
        return v.rstrip("/")

    @field_validator("jwt_secret_key")
    @classmethod
    def jwt_secret_key_must_be_strong(cls, v: str) -> str:
        if not v.strip():
            raise ValueError(
                "JWT_SECRET_KEY не задан. Укажите FAMILY_TREE_JWT_SECRET_KEY в .env "
                "(минимум 32 символа, например вывод: python -c \"import secrets; print(secrets.token_urlsafe(32))\")"
            )
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
