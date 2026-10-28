"""Общие фикстуры: приложение на временной SQLite-базе и tmp media dir.

Реальные .env/.db проекта не трогаются: pydantic-settings даёт фактическим
переменным окружения приоритет над .env, поэтому тесты задают свои.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Тестовые секреты (до импорта app.*)
os.environ.setdefault("FAMILY_TREE_JWT_SECRET_KEY", "t" * 48)
os.environ.setdefault("FAMILY_TREE_VIEW_TOKEN", "test-view-token-0123456789abcdef")
os.environ.setdefault("FAMILY_TREE_EDIT_TOKEN", "test-edit-token-0123456789abcdef")
# через TestClient нет реальных IP/прокси — защиту от перебора не включаем,
# её логика покрыта юнит-тестами напрямую
os.environ["FAMILY_TREE_ENABLE_RATE_LIMITING"] = "false"
os.environ["FAMILY_TREE_ENABLE_BRUTE_FORCE_PROTECTION"] = "false"
# боевой .env (ROOT_PATH двери, TRUST_PROXY) не должен влиять на тесты —
# часть тестов импортирует app.* до фикстуры app_env
os.environ["FAMILY_TREE_ROOT_PATH"] = ""
os.environ["FAMILY_TREE_TRUST_PROXY"] = "false"
os.environ["FAMILY_TREE_PRIORITY_FAMILY"] = ""


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    """Изолированная база + media dir; перезагружает модули app.*."""
    db_file = tmp_path / "test_family.db"
    monkeypatch.setenv("FAMILY_TREE_DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("FAMILY_TREE_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("FAMILY_TREE_OBSIDIAN_EXPORT_DIR", str(tmp_path / "export"))
    # боевой .env (например ROOT_PATH двери nginx) не должен влиять на тесты
    monkeypatch.setenv("FAMILY_TREE_ROOT_PATH", "")
    monkeypatch.setenv("FAMILY_TREE_TRUST_PROXY", "false")

    for mod in list(sys.modules):
        if mod.startswith("app"):
            del sys.modules[mod]

    from fastapi.testclient import TestClient

    from app.main import app  # noqa: F401

    # with-контекст обязателен: lifespan создаёт таблицы (init_db)
    with TestClient(app) as client:
        yield client


@pytest.fixture()
def viewer_client(app_env):
    """Клиент с cookie роли viewer (вход по ссылке-ключу)."""
    from app.config import settings

    resp = app_env.get(f"/api/v1/auth/link/{settings.view_token}", follow_redirects=False)
    assert resp.status_code == 303, resp.text
    return app_env


@pytest.fixture()
def editor_client(app_env):
    """Клиент с cookie роли editor."""
    from app.config import settings

    resp = app_env.get(f"/api/v1/auth/link/{settings.edit_token}", follow_redirects=False)
    assert resp.status_code == 303, resp.text
    return app_env
