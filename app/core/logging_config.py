"""Structured JSON logging configuration.

In production, every log line is a JSON object so it can be ingested by
CloudWatch / ELK / Datadog without extra parsing.  In development, a human-
readable format is used instead.

Each log record automatically includes:
  - timestamp (ISO-8601)
  - level
  - logger name
  - message
  - request_id  (when injected via logging.LoggerAdapter or structlog)
"""
from __future__ import annotations

import logging
import sys

from app.core.config import get_settings


class _JsonFormatter(logging.Formatter):
    """Minimal JSON log formatter (avoids requiring python-json-logger at import time)."""

    def format(self, record: logging.LogRecord) -> str:
        import json
        import traceback

        payload: dict = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include exception info when present
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Include any extra fields attached to the record
        for key, value in record.__dict__.items():
            if key not in {
                "args", "asctime", "created", "exc_info", "exc_text", "filename",
                "funcName", "id", "levelname", "levelno", "lineno", "module",
                "msecs", "message", "msg", "name", "pathname", "process",
                "processName", "relativeCreated", "stack_info", "thread",
                "threadName",
            }:
                payload[key] = value

        return json.dumps(payload, default=str)


def configure_logging() -> None:
    settings = get_settings()

    root = logging.getLogger()
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)

    if settings.ENVIRONMENT == "production":
        handler.setFormatter(_JsonFormatter())
        root.setLevel(logging.INFO)
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s  %(message)s")
        )
        root.setLevel(logging.DEBUG)

    root.addHandler(handler)

    # Silence noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("pdfminer").setLevel(logging.WARNING)
    logging.getLogger("python_multipart").setLevel(logging.WARNING)
