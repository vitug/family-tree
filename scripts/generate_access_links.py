#!/usr/bin/env python
"""Генератор ссылок доступа для деплоя.

Создаёт (при необходимости) длинные случайные view/edit-токены и JWT-секрет
в .env, затем печатает два адреса:

  🔗 только чтение:  http://<host>/api/v1/auth/link/<view-token>
  ✏️  редактирование: http://<host>/api/v1/auth/link/<edit-token>

Открытие ссылки само ставит httpOnly cookie с JWT — вводить токены не нужно.
Ссылки — секрет: доступ по ссылке равен доступу по токену.

Использование:
  python scripts/generate_access_links.py [--base-url http://192.168.1.5:8000]
      [--force]   перегенерировать токены (старые ссылки отзовутся)
      [--show]    только показать текущие ссылки, ничего не менять

Токены генерируются только если их нет или они слабые (dev-значения/короче
32 символов) — существующие сильные токены не трогаются.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

DEV_TOKENS = {"family-view-token-dev", "secret-edit-token-dev", "secret", "family"}
MIN_LEN = 32


def read_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def write_env(path: Path, env: dict[str, str]) -> None:
    lines = []
    for key, value in env.items():
        # значения токенов без кавычек: pydantic-settings читает как есть
        if any(c in value for c in " #"):
            value = f'"{value}"'
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # Windows: права через ACL, не критично


def is_strong(value: str | None) -> bool:
    return bool(value) and len(value) >= MIN_LEN and value not in DEV_TOKENS


def ensure_token(env: dict[str, str], key: str, force: bool) -> tuple[str, bool]:
    """Вернуть (токен, сгенерирован_ли заново)."""
    current = env.get(key)
    if not force and is_strong(current):
        return current, False
    new = secrets.token_urlsafe(32)  # ~43 символа, URL-safe
    env[key] = new
    return new, True


def main() -> int:
    parser = argparse.ArgumentParser(description="Ссылки доступа family tree")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Базовый адрес деплоя, например http://192.168.1.5:8000",
    )
    parser.add_argument("--force", action="store_true", help="Перегенерировать токены")
    parser.add_argument("--show", action="store_true", help="Только показать ссылки")
    args = parser.parse_args()

    if args.show and args.force:
        print("Ошибка: --show и --force несовместимы", file=sys.stderr)
        return 2

    env = read_env(ENV_PATH)

    if not args.show:
        view, view_new = ensure_token(env, "FAMILY_TREE_VIEW_TOKEN", args.force)
        edit, edit_new = ensure_token(env, "FAMILY_TREE_EDIT_TOKEN", args.force)
        jwt_new = False
        if args.force or not is_strong(env.get("FAMILY_TREE_JWT_SECRET_KEY")):
            env["FAMILY_TREE_JWT_SECRET_KEY"] = secrets.token_urlsafe(32)
            jwt_new = True
        write_env(ENV_PATH, env)
    else:
        view = env.get("FAMILY_TREE_VIEW_TOKEN", "")
        edit = env.get("FAMILY_TREE_EDIT_TOKEN", "")

    base = args.base_url.rstrip("/")

    print()
    print("═" * 72)
    print("  Ссылки доступа семейного дерева (DEPLOY)")
    print("═" * 72)
    if view:
        print(f"\n🔗 Только чтение (viewer):\n  {base}/api/v1/auth/link/{quote(view)}")
    if edit:
        print(f"\n✏️  Редактирование (editor):\n  {base}/api/v1/auth/link/{quote(edit)}")
    print("\nОткрой ссылку в браузере один раз — дальше сессия держится на cookie")
    print("(по умолчанию 24 ч, продлевается /auth/refresh). Никакие токены вводить не нужно.")
    print("⚠️  Ссылки равносильны паролям: не публиковать, передавать лично.")

    if not args.show:
        flags = []
        if view_new:
            flags.append("view-токен")
        if edit_new:
            flags.append("edit-токен")
        if jwt_new:
            flags.append("JWT-секрет")
        if flags:
            print(f"\n[сгенерировано заново: {', '.join(flags)}] — старые ссылки/сессии отозваны")
            print(f"  .env обновлён: {ENV_PATH}")
        else:
            print("\n[токены не менялись — существующие ссылки продолжают работать]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
