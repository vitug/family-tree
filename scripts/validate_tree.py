#!/usr/bin/env python3
"""Полная проверка дерева: python scripts/validate_tree.py [--db sqlite:///path.db].

Вызывать после seed_demo.py, импорта GEDCOM/Gramps или просто для аудита.
Ошибки — генеалогические противоречия (exit 1); предупреждения — инфо
(фамилии/изоляты/возраст, не блокируют).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    db_url = None
    args = sys.argv[1:]
    if "--db" in args:
        db_url = args[args.index("--db") + 1]

    if db_url:
        import os

        os.environ.setdefault("FAMILY_TREE_DATABASE_URL", db_url)
    # иначе берётся из .env приложения

    from app.database import SessionLocal
    from app.services.tree_validator import validate_tree

    with SessionLocal() as db:
        report = validate_tree(db)

    s = report["stats"]
    print(
        f"Люди: {s['people']} | связи: {s['relationships']} "
        f"(родители: {s['parents']}, супруги: {s['spouse_pairs']}) | "
        f"с фото: {s['with_photo']}"
    )
    if report["errors"]:
        print(f"ОШИБКИ ({len(report['errors'])}):")
        for e in report["errors"]:
            print("  !", e)
    else:
        print("Ошибок нет.")
    if report["warnings"]:
        print(f"Предупреждения ({len(report['warnings'])}):")
        for w in report["warnings"]:
            print("  i", w)
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
