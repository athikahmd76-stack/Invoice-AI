"""Python logging setup for the application."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from config import AppConfig


def setup_logger(config: AppConfig) -> logging.Logger:
    config.log_dir.mkdir(parents=True, exist_ok=True)
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
    logger = logging.getLogger("invoiceai")
    if logger.handlers:
        return logger
    level = getattr(logging, config.log_level, logging.INFO)
    logger.setLevel(level)

    formatter = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", "%Y-%m-%d %H:%M:%S")

    file_handler = RotatingFileHandler(config.log_dir / "invoiceai.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(f"invoiceai.{name}" if name else "invoiceai")