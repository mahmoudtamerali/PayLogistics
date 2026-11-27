"""
Input validation utilities for SecureCare backend.

Implements strict validation for all user-supplied inputs including
UUIDs, timestamps, emails, phone numbers, file metadata, and
request bodies.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from common.config import get_config
from common.exceptions import (
    BadRequestError,
    PayloadTooLargeError,
    ValidationError,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_FULLNAME_LENGTH = 150
MAX_TITLE_LENGTH = 120
MAX_NOTE_LENGTH = 5000
MAX_FILENAME_LENGTH = 255
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MiB

ALLOWED_CONTENT_TYPES = frozenset({
    "application/pdf",
    "image/png",
    "image/jpeg",
})

ALLOWED_FILE_EXTENSIONS = frozenset({
    ".pdf", ".png", ".jpg", ".jpeg",
})

VALID_VISIT_TYPES = frozenset({"consultation", "follow_up", "lab_visit"})
VALID_APPOINTMENT_STATUSES = frozenset({"BOOKED", "CANCELLED", "COMPLETED"})
VALID_FILE_STATUSES = frozenset({"PENDING_SCAN", "CLEAN", "QUARANTINED"})

# Valid state transitions for appointments
APPOINTMENT_TRANSITIONS: dict[str, set[str]] = {
    "BOOKED": {"CANCELLED", "COMPLETED"},
    "CANCELLED": set(),  # Terminal state
    "COMPLETED": set(),  # Terminal state
}

# Email regex (RFC 5322 simplified)
_EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9]"
    r"(?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)

# International phone regex (E.164 format)
_PHONE_REGEX = re.compile(r"^\+[1-9]\d{1,14}$")

# UUID v4 regex
_UUID_REGEX = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Date format YYYY-MM-DD
_DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# ISO 8601 timestamp
_ISO_TIMESTAMP_REGEX = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)

# Safe filename characters
_SAFE_FILENAME_REGEX = re.compile(r"^[a-zA-Z0-9._\- ]{1,255}$")

# File magic bytes for signature validation
FILE_SIGNATURES: dict[str, list[bytes]] = {
    "application/pdf": [b"%PDF-"],
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "image/jpeg": [b"\xff\xd8\xff"],
}


# ---------------------------------------------------------------------------
# Body parsing
# ---------------------------------------------------------------------------

def parse_request_body(event: dict[str, Any]) -> dict[str, Any]:
    """
    Safely parse JSON request body from API Gateway event.

    Args:
        event: API Gateway event.

    Returns:
        Parsed JSON body as a dictionary.

    Raises:
        BadRequestError: If the body is missing, too large, or malformed.
    """
    body = event.get("body")
    if body is None:
        raise BadRequestError("Request body is required")

    if isinstance(body, str):
        cfg = get_config()
        if len(body) > cfg.MAX_REQUEST_BODY_BYTES:
            raise PayloadTooLargeError(
                f"Request body exceeds maximum size of {cfg.MAX_REQUEST_BODY_BYTES} bytes"
            )
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            raise BadRequestError("Invalid JSON in request body")
    elif isinstance(body, dict):
        parsed = body
    else:
        raise BadRequestError("Unsupported request body format")

    if not isinstance(parsed, dict):
        raise BadRequestError("Request body must be a JSON object")

    return parsed


def parse_optional_body(event: dict[str, Any]) -> dict[str, Any] | None:
    """Parse body if present, return None if absent."""
    body = event.get("body")
    if body is None or body == "":
        return None
    return parse_request_body(event)


# ---------------------------------------------------------------------------
# Field validators
# ---------------------------------------------------------------------------

def validate_uuid(value: str, field_name: str = "id") -> str:
    """Validate a UUID v4 string."""
    if not value or not isinstance(value, str):
        raise ValidationError(f"{field_name} is required")
    value = value.strip()
    if not _UUID_REGEX.match(value):
        raise ValidationError(f"{field_name} must be a valid UUID v4")
    return value


def validate_or_generate_uuid(value: str | None = None, field_name: str = "id") -> str:
    """Validate UUID if provided, or generate a new one."""
    if value:
        return validate_uuid(value, field_name)
    return str(uuid.uuid4())


def validate_email(value: str) -> str:
    """Validate email format."""
    if not value or not isinstance(value, str):
        raise ValidationError("Email is required")
    value = value.strip().lower()
    if len(value) > 254:
        raise ValidationError("Email address too long")
    if not _EMAIL_REGEX.match(value):
        raise ValidationError("Invalid email format")
    return value


def validate_phone(value: str) -> str:
    """Validate international phone number (E.164 format)."""
    if not value or not isinstance(value, str):
        raise ValidationError("Phone number is required")
    value = value.strip()
    if not _PHONE_REGEX.match(value):
        raise ValidationError("Phone number must be in E.164 format (e.g. +353851234567)")
    return value


def validate_date(value: str, field_name: str = "date") -> str:
    """Validate YYYY-MM-DD date string."""
    if not value or not isinstance(value, str):
        raise ValidationError(f"{field_name} is required")
    value = value.strip()
    if not _DATE_REGEX.match(value):
        raise ValidationError(f"{field_name} must be in YYYY-MM-DD format")
    # Verify it's a real date
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValidationError(f"{field_name} is not a valid date")
    return value


def validate_iso_timestamp(value: str, field_name: str = "timestamp") -> str:
    """Validate ISO 8601 timestamp."""
    if not value or not isinstance(value, str):
        raise ValidationError(f"{field_name} is required")
    value = value.strip()
    if not _ISO_TIMESTAMP_REGEX.match(value):
        raise ValidationError(f"{field_name} must be an ISO 8601 timestamp")
    # Verify parseable
    try:
        datetime.fromisoformat(value)
    except ValueError:
        raise ValidationError(f"{field_name} is not a valid timestamp")
    return value


def parse_iso_to_utc(value: str) -> datetime:
    """Parse ISO 8601 string to UTC datetime."""
    return datetime.fromisoformat(value).astimezone(UTC)


def derive_day_from_timestamp(iso_timestamp: str) -> str:
    """Derive YYYY-MM-DD day string from ISO timestamp in UTC."""
    dt = parse_iso_to_utc(iso_timestamp)
    return dt.strftime("%Y-%m-%d")


def validate_string(
    value: Any,
    field_name: str,
    min_length: int = 1,
    max_length: int = 1000,
    required: bool = True,
) -> str | None:
    """Validate a string field."""
    if value is None or (isinstance(value, str) and len(value.strip()) == 0):
        if required:
            raise ValidationError(f"{field_name} is required")
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string")
    value = value.strip()
    if len(value) < min_length:
        raise ValidationError(f"{field_name} must be at least {min_length} characters")
    if len(value) > max_length:
        raise ValidationError(f"{field_name} must not exceed {max_length} characters")
    return value


def validate_visit_type(value: str) -> str:
    """Validate appointment visit type."""
    if value not in VALID_VISIT_TYPES:
        raise ValidationError(
            f"visitType must be one of: {', '.join(sorted(VALID_VISIT_TYPES))}"
        )
    return value


def validate_appointment_status(value: str) -> str:
    """Validate appointment status."""
    if value not in VALID_APPOINTMENT_STATUSES:
        raise ValidationError(
            f"status must be one of: {', '.join(sorted(VALID_APPOINTMENT_STATUSES))}"
        )
    return value


def validate_status_transition(current: str, target: str) -> None:
    """Validate appointment status transition."""
    allowed = APPOINTMENT_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ValidationError(
            f"Cannot transition from {current} to {target}. "
            f"Allowed transitions from {current}: {', '.join(sorted(allowed)) if allowed else 'none'}"
        )


def validate_file_content_type(content_type: str) -> str:
    """Validate file content type against allowlist."""
    if not content_type or not isinstance(content_type, str):
        raise ValidationError("contentType is required")
    content_type = content_type.strip().lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValidationError(
            f"contentType must be one of: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}"
        )
    return content_type


def validate_file_name(filename: str) -> str:
    """Validate filename for safety and allowed extensions."""
    if not filename or not isinstance(filename, str):
        raise ValidationError("fileName is required")
    filename = filename.strip()

    if len(filename) > MAX_FILENAME_LENGTH:
        raise ValidationError(f"fileName must not exceed {MAX_FILENAME_LENGTH} characters")

    if not _SAFE_FILENAME_REGEX.match(filename):
        raise ValidationError("fileName contains invalid characters")

    # Check for path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        raise ValidationError("fileName contains invalid path characters")

    # Check extension
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_FILE_EXTENSIONS:
        raise ValidationError(
            f"File extension must be one of: {', '.join(sorted(ALLOWED_FILE_EXTENSIONS))}"
        )

    return filename


def validate_file_size(size: Any) -> int:
    """Validate file size."""
    if size is None:
        raise ValidationError("sizeBytes is required")
    try:
        size_int = int(size)
    except (ValueError, TypeError):
        raise ValidationError("sizeBytes must be a number")
    if size_int <= 0:
        raise ValidationError("sizeBytes must be positive")
    if size_int > MAX_FILE_SIZE_BYTES:
        raise ValidationError(f"File size must not exceed {MAX_FILE_SIZE_BYTES} bytes (10 MiB)")
    return size_int


def validate_file_signature(header_bytes: bytes, content_type: str) -> bool:
    """Validate leading magic bytes against expected MIME content type."""
    signatures = FILE_SIGNATURES.get(content_type)
    if not signatures:
        return False
    return any(header_bytes.startswith(sig) for sig in signatures)


def validate_path_parameter(event: dict[str, Any], param_name: str) -> str:
    """Extract and validate a path parameter."""
    params = event.get("pathParameters") or {}
    value = params.get(param_name)
    if not value or not isinstance(value, str) or len(value.strip()) == 0:
        raise BadRequestError(f"Missing path parameter: {param_name}")
    return value.strip()


def validate_query_parameters(
    event: dict[str, Any],
    allowed: set[str],
) -> dict[str, str]:
    """Extract and validate query string parameters."""
    params = event.get("queryStringParameters") or {}
    result: dict[str, str] = {}
    for key, value in params.items():
        if key not in allowed:
            raise BadRequestError(f"Unsupported query parameter: {key}")
        if isinstance(value, str):
            result[key] = value.strip()
    return result


def sanitize_allowlisted_fields(
    data: dict[str, Any],
    allowed_fields: set[str],
) -> dict[str, Any]:
    """
    Return only allowed fields from input data.

    Prevents mass-assignment attacks by ensuring only explicitly
    permitted fields are accepted.
    """
    return {k: v for k, v in data.items() if k in allowed_fields}
