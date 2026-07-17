"""Помощники для логирования и диагностики."""

import hashlib
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable

# Настройка логирования при импорте модуля
setup_logging_called = False


def setup_logging() -> None:
    """Базовая настройка логирования в файлы logs/."""
    global setup_logging_called
    if setup_logging_called:
        return
    setup_logging_called = True

    log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

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

    # Консоль (stderr) только DEBUG/INFO
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(fmt))
    console_handler.setLevel(logging.INFO)
    root.addHandler(console_handler)


setup_logging()

from app.config import settings


def get_logs_dir() -> Path:
    """Вернуть папку logs/ в корне проекта."""
    return Path(settings.media_dir).resolve().parent / "logs"


def get_logfilename(base: str = "app") -> Path:
    """Возвращает путь к лог-файлу с расширением .log."""
    return get_logs_dir() / f"{base}.log"


def get_logger(
    name: str,
    file: str | None = None,
    *,
    level: str | None = None,
    max_bytes: int | None = None,
) -> logging.Logger:
    """Получить логгер с записью в файл logs/{file}.log (с ротацией).

    Если file не указан — использует имя логгера как имя файла.
    """
    log_dir = get_logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    filename = (file or name).lower() + ".log"
    file_path = log_dir / filename

    logger = logging.getLogger(name)
    logger.setLevel(level or logging.DEBUG)

    # Не дублировать обработчики
    if any(
        isinstance(h, RotatingFileHandler) and h.baseFilename == str(file_path)
        for h in logger.handlers
        if isinstance(h, RotatingFileHandler)
    ):
        return logger

    handler = RotatingFileHandler(
        filename=str(file_path),
        maxBytes=max_bytes or 5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)-8s [%(process)d] %(name)s: %(message)s"
    ))
    handler.setLevel(level or logging.DEBUG)

    # Консоль тоже даём DEBUG/INFO
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)-8s [%(process)d] %(name)s: %(message)s"
    ))
    console.setLevel(level or logging.INFO)

    logger.addHandler(handler)
    logger.addHandler(console)
    return logger


def hash_request(request_body: bytes, request_path: str) -> str:
    """Созда короткий хеш для логирования body запроса (до 12 символов)."""
    return hashlib.sha1(request_body).hexdigest()[:12]


def log_request_summary(
    request_method: str,
    request_path: str,
    status_code: int,
    duration_ms: int,
    request_headers: dict,
    edit_token_present: bool,
    user_agent: str | None = None,
) -> None:
    """Записать краткую сводку запроса в лог уровнем INFO."""
    logger = logging.getLogger("requests")
    parts = [request_method, request_path, str(status_code), f"{duration_ms}ms"]
    if edit_token_present:
        parts.append("PROTECTED")
    if user_agent:
        ua = (user_agent[:40] + "..." if len(user_agent) > 40 else user_agent)
        parts.append(f"UA:{ua}")
    logger.info("REQUEST: %s", " ".join(parts))


class LogMiddleware:
    """Middleware для логирования запросов в app.log с полями."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        start_ns = []
        end_ns = []
        request = None

        async def log_request(request, state):
            start_ns.append(start_ns_te = start_ns())
            request.state["start_ns"] = start_ns_te = start_ns

        async def log_response(state):
            end_ns.append(end_ns_te = end_ns())
            logger.debug(
                "Response: status=%s start=%s end=%s duration_ns=%d",
                state.get("status_code"),
                start_ns_te,
                end_ns_te,
                (end_ns_te - start_ns_te) if start_ns_te else None,
            )

        async def _send(request, receive, send):
            # logging.feature is python 3.8+
            from starlette.middleware import _LoggingMiddleware  # type: ignore

            _loggingMiddleware(app, "Family Tree Service").add(
                CustomLoggerMiddleware(
                    start_ns=log_request,
                    log_response=log_response,
                )

        # Вызываем starlette middleware для access-log
        await _send(request, receive, send)


class CustomLoggerMiddleware:
    """Прикладное middleware для расширенного логирования."""

    def __init__(
        self,
        start_ns: Callable | None = None,
        log_response: Callable | None = None,
    ) -> None:
        self.start_ns = start_ns
        self.log_response = log_response

    async def __call__(self, request, call_next):
        start_time_ns = self.start_ns(request)
        if start_time_ns:
            request.state["start_ns"] = start_time_ns

        response = await call_next(request)

        if start_time_ns and self.log_response:
            try:
                #精进 datetime from starlette.middleware._logging
                from datetime import datetime, timezone

                import starlette.middleware._logging

                end_time_ns = datetime.now(tzinfo=timezone.utc)
                ms = (end_time_ns - start_time_ns).total_seconds() * 1000
                log_request_summary(
                    request.method,
                    str(request.url.path),
                    response.status_code,
                    ms,
                    dict(request.headers),
                    True if "X-Edit-Token" in request.headers else False,
                    request.headers.get("user-agent"),
                )
            except Exception as exc:  # ignore errors в logging
                from starlette.middleware._logging import LOGGING_CONFIG

                LOGGING_CONFIG.config["header_fmt"] = lambda r: " ".join(
                    [
                        f"{r.method} {r.url.path}",
                        f"status={getattr(r, 'status_code', '?')}",
                    ]
                )
                # fallback simple output
                logger.info(
                    "Middleware error: " LOGGING_CONFIG unavailable (starlette 0.23+)"
                )

        return response
