"""Логгер с ротацией для всех HTTP-запросов и приложения."""

import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler



def setup_logging() -> None:
    """Настройка логирования в файлы logs/."""
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Каждый модуль свой logger с единой формат строкой
    fmt = "[%(asctime)s] %(levelname)-8s [%(process)d] %(name)s: %(message)s"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Файл для всего приложения
    app_file = log_dir / "app.log"
    app_handler = RotatingFileHandler(
        filename=str(app_file),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    app_handler.setFormatter(logging.Formatter(fmt))
    app_handler.setLevel(logging.DEBUG)
    root.addHandler(app_handler)

    # Консоль (stderr) только DEBUG/INFO в консоль
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(logging.Formatter(fmt))
    console_handler.setLevel(logging.INFO)
    root.addHandler(console_handler)

    # uvicorn и starlette не должны писать свои логи в наш app.log
    # Будет работать просто логгер для приложения

    # Перенаправить stderr uvicorn в консоль (без файла)
    #_stderr = logging.StreamHandler(sys.stderr)
    # uvicorn вставит свои handlers.


def get_access_logger() -> logging.Logger:
    """Логгер для записи access-лога (поступ к API)."""
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("access")
    logger.setLevel(logging.DEBUG)
    handler = RotatingFileHandler(
        filename=str(log_dir / "access.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    return logger


# Настроить логирование при импорте
setup_logging()
