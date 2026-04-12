import logging
from typing import Final

from app.core.config import settings

DEFAULT_FORMAT: Final[str] = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
_is_configured = False


def _parse_level(level_name: str) -> int:
    return getattr(logging, level_name.upper(), logging.INFO)


def configure_logging() -> None:
    global _is_configured
    if _is_configured:
        return

    log_level = _parse_level(settings.LOG_LEVEL)
    root_logger = logging.getLogger()

    # Avoid duplicate handlers when server frameworks manage logging themselves.
    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(DEFAULT_FORMAT))
        root_logger.addHandler(handler)

    root_logger.setLevel(log_level)

    # Keep SQL logs quiet by default; enable detail with SQL_ECHO=true when needed.
    sqlalchemy_level = logging.INFO if settings.SQL_ECHO else logging.WARNING
    logging.getLogger("sqlalchemy.engine").setLevel(sqlalchemy_level)

    _is_configured = True


logger = logging.getLogger("app")
