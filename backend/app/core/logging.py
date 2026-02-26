"""Structured logging setup."""

import logging
import json
from datetime import datetime
from typing import Any


class StructuredFormatter(logging.Formatter):
    """JSON-like structured log output."""

    def format(self, record: logging.LogRecord) -> str:
        base = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in ("name", "msg", "args", "created", "filename", "funcName",
                           "levelname", "levelno", "lineno", "module", "msecs",
                           "pathname", "process", "processName", "relativeCreated",
                           "stack_info", "exc_info", "exc_text", "thread", "threadName",
                           "message", "extra_fields"):
                if value is not None:
                    base[key] = value
        return json.dumps(base, default=str)


def get_logger(name: str) -> logging.Logger:
    """Return a structured logger instance."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
