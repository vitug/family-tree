"""Точка входа desktop-сборки (PyInstaller, family_tree.spec).

Запускает сервер локально (127.0.0.1:8123), генерирует .env со случайными
токенами при первом старте и открывает браузер с приложением.
"""
from __future__ import annotations

import os
import secrets
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

PORT = 8123


def app_base() -> Path:
    """Рабочая папка: рядом с exe (PyInstaller) или корень репозитория."""
    if getattr(sys, "frozen", False):  # PyInstaller
        return Path(sys.executable).parent
    return Path(__file__).parent


def ensure_env(base: Path) -> tuple[str, str]:
    """Создать .env со случайными токенами, если его нет. → (view, edit)."""
    env_path = base / ".env"
    if env_path.exists():
        vals = {}
        for ln in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in ln and not ln.startswith("#"):
                k, v = ln.split("=", 1)
                vals[k.strip()] = v.strip()
        return vals.get("FAMILY_TREE_VIEW_TOKEN", ""), vals.get("FAMILY_TREE_EDIT_TOKEN", "")
    view = secrets.token_urlsafe(18)
    edit = secrets.token_urlsafe(18)
    env_path.write_text(
        f"FAMILY_TREE_DATABASE_URL=sqlite:///{(base / 'family_tree.db').as_posix()}\n"
        f"FAMILY_TREE_MEDIA_DIR={(base / 'media').as_posix()}\n"
        f"FAMILY_TREE_JWT_SECRET_KEY={secrets.token_urlsafe(32)}\n"
        f"FAMILY_TREE_VIEW_TOKEN={view}\n"
        f"FAMILY_TREE_EDIT_TOKEN={edit}\n"
        f"FAMILY_TREE_JWT_ACCESS_TOKEN_EXPIRE_HOURS=720\n"
        f"FAMILY_TREE_LOG_LEVEL=INFO\n",
        encoding="utf-8",
    )
    return view, edit


def main() -> None:
    base = app_base()
    os.chdir(base)  # БД/медиа/логи — рядом с exe
    view, edit = ensure_env(base)

    from app.config import settings  # читает .env из рабочей папки
    from app.main import app  # импорт напрямую: PyInstaller не найдёт строку "app.main:app"
    import uvicorn

    print(f"Family Tree — рабочая папка: {base}")
    print(f"EDIT-ссылка (для редактирования): http://127.0.0.1:{PORT}/auth/link/{edit}")
    print("Запускаем сервер… (Ctrl+C — выход)")

    config = uvicorn.Config(
        app, host="127.0.0.1", port=PORT, log_level=settings.log_level.lower()
    )
    server = uvicorn.Server(config)

    def open_browser() -> None:
        # подождать, пока сервер примет соединения
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", PORT), timeout=0.3):
                    break
            except OSError:
                time.sleep(0.2)
        webbrowser.open(f"http://127.0.0.1:{PORT}/auth/link/{view}")

    threading.Thread(target=open_browser, daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
