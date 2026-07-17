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


settings = Settings()
