"""
Security utilities for SecureCare backend.

Provides security-related helpers including request sanitization,
safe error handling, and security headers.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from common.exceptions import BadRequestError, SecureCareError

logger = logging.getLogger("securecare.security")


def get_request_id(event: dict[str, Any]) -> str:
    """Extract the API Gateway request ID for correlation."""
    return event.get("requestContext", {}).get("requestId", "unknown")


def get_source_ip(event: dict[str, Any]) -> str:
    """Extract source IP from the API Gateway event."""
    return (
        event.get("requestContext", {})
        .get("identity", {})
        .get("sourceIp", "unknown")
    )


def get_http_method(event: dict[str, Any]) -> str:
    """Extract the HTTP method from the event."""
    return event.get("httpMethod", "").upper()


def get_resource_path(event: dict[str, Any]) -> str:
    """Extract the resource path from the event."""
    return event.get("resource", event.get("path", ""))


def get_path_parameters(event: dict[str, Any]) -> dict[str, str]:
    """Extract path parameters, defaulting to empty dict."""
    return event.get("pathParameters") or {}


def sanitize_log_message(message: str, max_length: int = 1000) -> str:
    """Sanitize a message for safe logging."""
    if not message:
        return ""
    # Remove potential control characters
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", message)
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length] + "...[TRUNCATED]"
    return cleaned


def mask_sensitive_value(value: str, visible_chars: int = 4) -> str:
    """Mask a sensitive value, showing only the last N characters."""
    if not value or len(value) <= visible_chars:
        return "****"
    return "*" * (len(value) - visible_chars) + value[-visible_chars:]


def validate_no_script_injection(value: str, field_name: str = "field") -> str:
    """Basic check for script injection in string values."""
    if not isinstance(value, str):
        return value
    # Check for common injection patterns
    dangerous_patterns = [
        r"<script",
        r"javascript:",
        r"on\w+\s*=",
        r"eval\s*\(",
        r"expression\s*\(",
    ]
    lower_value = value.lower()
    for pattern in dangerous_patterns:
        if re.search(pattern, lower_value):
            raise BadRequestError(f"Invalid characters in {field_name}")
    return value


def safe_error_message(error: Exception) -> str:
    """
    Return a safe error message suitable for client responses.

    Never exposes stack traces, internal paths, or AWS-specific details.
    """
    if isinstance(error, SecureCareError):
        return error.message
    # For unexpected errors, return a generic message
    return "An internal error occurred"
