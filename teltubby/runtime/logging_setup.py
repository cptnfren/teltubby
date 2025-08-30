"""Logging setup for structured JSON logs and rotation.

Provides `setup_logging(AppConfig)` which configures root logging, a JSON
formatter for stdout, and a rotating file handler with size and backup count
driven by environment configuration.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from typing import Any

from pythonjsonlogger import jsonlogger

from .config import AppConfig


def setup_logging(config: AppConfig) -> None:
    """Configure global logging with JSON formatter and rotation.

    Args:
        config: Application configuration to source levels and rotation limits.
    """
    level = getattr(logging, config.log_level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear existing handlers (avoid duplicates on reload)
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    # JSON stdout handler
    stdout_handler = logging.StreamHandler()
    json_formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"levelname": "level"},
    )
    stdout_handler.setFormatter(json_formatter)
    root_logger.addHandler(stdout_handler)

    # Rotating file handler (optional)
    logs_dir = os.getenv("LOGS_DIR", "/app/logs")
    try:
        os.makedirs(logs_dir, exist_ok=True)
    except Exception:
        pass

    file_handler = logging.handlers.RotatingFileHandler(
        filename=os.path.join(logs_dir, "teltubby.log"),
        maxBytes=config.log_rotate_max_bytes,
        backupCount=config.log_rotate_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(json_formatter)
    root_logger.addHandler(file_handler)

    logging.getLogger("teltubby").info(
        "logging configured",
        extra={
            "level": config.log_level,
            "rotate_max_bytes": config.log_rotate_max_bytes,
            "rotate_backup_count": config.log_rotate_backup_count,
        },
    )

