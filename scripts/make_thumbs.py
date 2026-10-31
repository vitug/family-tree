#!/usr/bin/env python3
"""Бэкфилл миниатюр t_<имя>.jpg для уже загруженных фото (прод и дев).

Запуск из корня приложения:
    python scripts/make_thumbs.py          # создать отсутствующие
    python scripts/make_thumbs.py --force  # пересоздать все
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.routers.media import _make_thumbnail  # noqa: E402


def main() -> None:
    force = "--force" in sys.argv
    files_root = Path(settings.media_dir) / "files"
    made, skipped, total_bytes = 0, 0, 0
    for img in sorted(files_root.rglob("*")):
        if img.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif"}:
            continue
        if img.name.startswith("t_"):
            continue
        thumb = img.parent / f"t_{img.stem}.jpg"
        if thumb.is_file() and not force:
            skipped += 1
            continue
        if _make_thumbnail(img):
            made += 1
            total_bytes += thumb.stat().st_size
    print(f"миниатюр создано: {made} (~{total_bytes // 1024} КБ), уже было: {skipped}")


if __name__ == "__main__":
    main()
