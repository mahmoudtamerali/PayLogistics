"""
Structured logging configuration for SecureCare backend.

Uses Python standard logging with JSON formatting for CloudWatch
integration. Ensures sensitive data is never logged.
"""

import json
import logging
import sys
from typing import Any


class SecureCareJsonFormatter(logging.Formatter):
    """JSON formatter that produces CloudWatch-compatible structured logs."""

    SENSITIVE_KEYS = frozenset({
        "password", "token", "access_token", "refresh_token", "id_token",
        "authorization", "secret", "credential", "aws_secret_access_key",
        "aws_session_token", "note", "clinical_note", "note_content",
    })

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add request_id if available
        if hasattr(record, "request_id"):
            log_entry["request_id"] = record.request_id

        # Add extra fields, filtering sensitive data
        if hasattr(record, "extra_data") and isinstance(record.extra_data, dict):
            safe_extra = self._sanitize(record.extra_data)
            log_entry["data"] = safe_extra

        # Add exception info if present
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = {
                "type": type(record.exc_info[1]).__name__,
                "message": str(record.exc_info[1]),
            }

        return json.dumps(log_entry, default=str)

    def _sanitize(self, data: dict[str, Any]) -> dict[str, Any]:
        """Remove sensitive keys from log data."""
        sanitized: dict[str, Any] = {}
        for key, value in data.items():
            if key.lower() in self.SENSITIVE_KEYS:
                sanitized[key] = "[REDACTED]"
            elif isinstance(value, dict):
                sanitized[key] = self._sanitize(value)
            else:
                sanitized[key] = value
        return sanitized


def setup_logging(
    level: int = logging.INFO,
    request_id: str | None = None,
) -> logging.Logger:
    """
    Configure and return the root SecureCare logger.

    Args:
        level: Logging level (default INFO).
        request_id: Correlation ID for the current request.

    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger("securecare")

    # Avoid adding duplicate handlers
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(SecureCareJsonFormatter())
        logger.addHandler(handler)

    logger.setLevel(level)
    logger.propagate = False
    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Return a child logger under the securecare namespace.

    Args:
        name: Logger name suffix (e.g. 'auth', 'patients').
    """
    return logging.getLogger(f"securecare.{name}")
