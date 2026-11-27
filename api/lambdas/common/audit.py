"""
Audit event creation and storage for SecureCare backend.

Implements append-only audit logging with conditional writes to prevent
tampering. Uses the existing securecare-audit DynamoDB table schema.

IMPORTANT:
- Audit records are append-only at the application level.
- Uses conditional PutItem with attribute_not_exists to prevent overwrites.
- Application roles must not update or delete existing audit records.
- Both ALLOWED and DENIED outcomes are recorded.
- Never store passwords, tokens, secrets, or full clinical notes in audit details.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from common.auth import AuthContext
from common.config import get_config
from common.dynamodb import put_item
from common.exceptions import ConflictError, InternalError

logger = logging.getLogger("securecare.audit")

# Valid outcome values
OUTCOME_ALLOWED = "ALLOWED"
OUTCOME_DENIED = "DENIED"

# System patient_id for events without a patient subject
SYSTEM_PATIENT_ID = "SYSTEM"


def _generate_event_id() -> str:
    """Generate a unique, sortable event_id using ISO timestamp + UUID."""
    now = datetime.now(UTC)
    return f"{now.strftime('%Y%m%dT%H%M%S%f')}#{uuid.uuid4().hex[:12]}"


def _calculate_expires_at(retention_days: int) -> int:
    """
    Calculate expiresAt as an integer Unix epoch timestamp.

    Schema v3 formula: int(now.timestamp()) + retention_days * 86400
    (default retention is 1826 days ≈ 5 years).
    """
    now = datetime.now(UTC)
    return int(now.timestamp()) + int(retention_days) * 86400


def write_audit_event(
    patient_id: str,
    actor_id: str,
    actor_role: str,
    action: str,
    outcome: str,
    source_ip: str,
    request_id: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> str | None:
    """
    Write an audit event to the securecare-audit table.

    Uses conditional PutItem with attribute_not_exists to ensure
    append-only behavior.

    Args:
        patient_id: Patient subject of the event, or "SYSTEM" for system events.
        actor_id: Cognito sub of the user performing the action.
        actor_role: Application role of the actor.
        action: Description of the action performed.
        outcome: "ALLOWED" or "DENIED".
        source_ip: Source IP address of the request.
        request_id: API Gateway request correlation ID.
        resource_type: Type of resource affected (optional).
        resource_id: ID of the resource affected (optional).
        detail: Additional context (must not contain secrets or PHI).

    Returns:
        The event_id of the created audit record, or None on failure.
    """
    cfg = get_config()
    event_id = _generate_event_id()
    now = datetime.now(UTC)

    # Validate actor_role
    valid_roles = {"patient", "doctor", "staff", "admin"}
    if actor_role not in valid_roles:
        logger.error("Invalid actor_role for audit: %s", actor_role)
        actor_role = "unknown"

    # Validate outcome
    if outcome not in {OUTCOME_ALLOWED, OUTCOME_DENIED}:
        logger.error("Invalid outcome for audit: %s", outcome)
        outcome = OUTCOME_DENIED

    # Sanitize detail – remove any sensitive data that may have leaked
    safe_detail = _sanitize_detail(detail) if detail else None

    item: dict[str, Any] = {
        "patient_id": patient_id,
        "event_id": event_id,
        "day": now.strftime("%Y-%m-%d"),
        "timestamp": now.isoformat(),
        "actor_id": actor_id,
        "actor_role": actor_role,
        "action": action,
        "outcome": outcome,
        "source_ip": source_ip,
        "request_id": request_id,
        "expiresAt": _calculate_expires_at(cfg.AUDIT_RETENTION_DAYS),
    }

    if resource_type:
        item["resource_type"] = resource_type
    if resource_id:
        item["resource_id"] = resource_id
    if safe_detail:
        item["detail"] = safe_detail

    try:
        put_item(
            table_name=cfg.AUDIT_TABLE,
            item=item,
            condition_expression="attribute_not_exists(event_id)",
        )
        return event_id
    except (InternalError, ConflictError, RuntimeError, OSError) as e:
        # Audit write failures should not break the main operation,
        # but must be logged prominently for investigation.
        logger.error(
            "CRITICAL: Failed to write audit event: action=%s error=%s",
            action,
            str(e),
        )
        return None


def log_access(
    auth: AuthContext,
    patient_id: str,
    action: str,
    outcome: str = OUTCOME_ALLOWED,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> str | None:
    """
    Convenience wrapper to log an access event using the auth context.

    Args:
        auth: Authenticated user context.
        patient_id: Patient subject of the event.
        action: Description of the action.
        outcome: "ALLOWED" or "DENIED".
        resource_type: Type of resource.
        resource_id: ID of the resource.
        detail: Additional context.

    Returns:
        The event_id or None.
    """
    return write_audit_event(
        patient_id=patient_id,
        actor_id=auth.sub,
        actor_role=auth.primary_role,
        action=action,
        outcome=outcome,
        source_ip=auth.source_ip,
        request_id=auth.request_id,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail,
    )


def log_denied(
    auth: AuthContext,
    patient_id: str,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> str | None:
    """Log a DENIED access attempt."""
    return log_access(
        auth=auth,
        patient_id=patient_id,
        action=action,
        outcome=OUTCOME_DENIED,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail,
    )


def log_system_event(
    action: str,
    outcome: str = OUTCOME_ALLOWED,
    source_ip: str = "internal",
    request_id: str = "system",
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> str | None:
    """Log a system-level event without a patient subject."""
    return write_audit_event(
        patient_id=SYSTEM_PATIENT_ID,
        actor_id="SYSTEM",
        actor_role="admin",
        action=action,
        outcome=outcome,
        source_ip=source_ip,
        request_id=request_id,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Sensitive data sanitization
# ---------------------------------------------------------------------------

_SENSITIVE_KEYS = frozenset({
    "password", "token", "access_token", "refresh_token", "id_token",
    "authorization", "secret", "credential", "aws_secret_access_key",
    "aws_session_token", "note", "clinical_note", "note_content",
    "ssn", "social_security", "credit_card",
})


def _sanitize_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """Remove sensitive data from audit detail."""
    sanitized: dict[str, Any] = {}
    for key, value in detail.items():
        if key.lower() in _SENSITIVE_KEYS:
            sanitized[key] = "[REDACTED]"
        elif isinstance(value, dict):
            sanitized[key] = _sanitize_detail(value)
        elif isinstance(value, str) and len(value) > 500:
            # Truncate large string values to prevent PHI leakage
            sanitized[key] = value[:200] + "...[TRUNCATED]"
        else:
            sanitized[key] = value
    return sanitized
