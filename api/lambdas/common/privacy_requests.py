"""
Privacy request tracking for SecureCare backend.

Implements durable persistence and ownership-checked lookup of EXPORT and
ERASURE requests in the securecare-data-requests DynamoDB table
(PK: patient_id, SK: request_id).

Schema v3 constraints:
  - request_id format: <UTC ISO timestamp>#<UUID>
  - request_type: EXPORT | ERASURE
  - status: PENDING_APPROVAL | APPROVED | PROCESSING | COMPLETED | REJECTED
  - A patient can read only requests inside their own patient_id partition.
  - Admins may read any patient's request only when an explicit patient_id
    query parameter is supplied (compliance oversight path).
  - Doctors and staff are always denied with 403 + a DENIED audit event.

This module lives in common/ so every Lambda package (including the
individual export/erase ZIPs) ships it without extra build wiring.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from common.audit import OUTCOME_ALLOWED, log_access, log_denied
from common.auth import AuthContext
from common.authorization import check_permission
from common.config import get_config
from common.dynamodb import get_item, put_item
from common.exceptions import ForbiddenError, NotFoundError, ValidationError
from common.identity import resolve_patient_id
from common.validation import validate_string

logger = logging.getLogger("securecare.privacy_requests")

# Allowlisted values (Schema v3 section 1.3)
REQUEST_TYPES = frozenset({"EXPORT", "ERASURE"})
REQUEST_STATUSES = frozenset({
    "PENDING_APPROVAL",
    "APPROVED",
    "PROCESSING",
    "COMPLETED",
    "REJECTED",
})

# Roles that may never read privacy requests
_DENIED_READ_ROLES = frozenset({"doctor", "staff"})

# Attributes returned to API callers (whitelist – never leak export_url etc.)
_RESPONSE_FIELDS = (
    "patient_id",
    "request_id",
    "request_type",
    "status",
    "reason",
    "requested_at",
    "approved_by",
    "completed_at",
)


def generate_request_id() -> str:
    """Generate a Schema v3 request_id: <UTC ISO timestamp>#<UUID>."""
    now = datetime.now(UTC)
    timestamp = now.isoformat().replace("+00:00", "Z")
    return f"{timestamp}#{uuid.uuid4()}"


def now_iso() -> str:
    """Current UTC time as ISO-8601 with Z suffix."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def persist_privacy_request(
    patient_id: str,
    request_id: str,
    request_type: str,
    status: str,
    *,
    reason: str | None = None,
    requested_at: str,
    completed_at: str | None = None,
) -> dict[str, Any]:
    """
    Persist a tracking item in REQUESTS_TABLE.

    Uses a conditional PutItem so an existing (patient_id, request_id)
    pair can never be silently overwritten.
    """
    if request_type not in REQUEST_TYPES:
        raise ValidationError(f"Invalid request_type: {request_type}")
    if status not in REQUEST_STATUSES:
        raise ValidationError(f"Invalid status: {status}")

    cfg = get_config()
    item: dict[str, Any] = {
        "patient_id": patient_id,
        "request_id": request_id,
        "request_type": request_type,
        "status": status,
        "requested_at": requested_at,
    }
    if reason is not None:
        item["reason"] = reason
    if completed_at is not None:
        item["completed_at"] = completed_at

    put_item(
        table_name=cfg.REQUESTS_TABLE,
        item=item,
        condition_expression=(
            "attribute_not_exists(patient_id) AND attribute_not_exists(request_id)"
        ),
    )
    return item


def get_privacy_request(patient_id: str, request_id: str) -> dict[str, Any] | None:
    """Fetch a single tracking item by its full primary key."""
    cfg = get_config()
    return get_item(
        cfg.REQUESTS_TABLE,
        {"patient_id": patient_id, "request_id": request_id},
    )


def resolve_request_lookup(
    auth: AuthContext,
    request_id: str,
    query_patient_id: str | None = None,
) -> dict[str, Any]:
    """
    Ownership-checked lookup of a privacy request.

    Rules:
      - patient: only their own partition (patient_id == sub); an explicit
        patient_id for another patient is 403 + DENIED audit.
      - admin:   any patient, but only when patient_id is supplied
        (compliance oversight). Missing patient_id is 403.
      - doctor/staff: always 403 + DENIED audit.
      - not found: 404 (a bare request_id is never proof of ownership).
    """
    request_id = validate_string(
        request_id, "request_id", min_length=1, max_length=128
    ) or ""
    role = auth.primary_role

    if role in _DENIED_READ_ROLES:
        log_denied(
            auth,
            query_patient_id or "SYSTEM",
            "GET_PRIVACY_REQUEST_DENIED_ROLE",
            resource_type="privacy_request",
            resource_id=request_id,
            detail={"actor_role": role},
        )
        raise ForbiddenError(
            "Only the requesting patient or an admin may view privacy requests"
        )

    if role == "patient":
        own_patient_id = resolve_patient_id(auth)
        if query_patient_id and query_patient_id != own_patient_id:
            log_denied(
                auth,
                query_patient_id,
                "GET_PRIVACY_REQUEST_CROSS_PATIENT",
                resource_type="privacy_request",
                resource_id=request_id,
            )
            raise ForbiddenError("Patients may only view their own privacy requests")
        patient_id = own_patient_id
    elif role == "admin":
        if not query_patient_id:
            log_denied(
                auth,
                "SYSTEM",
                "GET_PRIVACY_REQUEST_MISSING_SCOPE",
                resource_type="privacy_request",
                resource_id=request_id,
            )
            raise ForbiddenError(
                "patient_id query parameter is required for admin compliance lookup"
            )
        patient_id = query_patient_id
    else:
        # Unknown/unsupported role – fail closed.
        log_denied(
            auth,
            query_patient_id or "SYSTEM",
            "GET_PRIVACY_REQUEST_UNKNOWN_ROLE",
            resource_type="privacy_request",
            resource_id=request_id,
            detail={"actor_role": role},
        )
        raise ForbiddenError()

    # Enforces patient/admin-only matrix (export:check_status).
    check_permission(auth, "export:check_status")

    item = get_privacy_request(patient_id, request_id)
    if not item:
        raise NotFoundError("Privacy request not found")

    log_access(
        auth=auth,
        patient_id=patient_id,
        action="GET_PRIVACY_REQUEST",
        outcome=OUTCOME_ALLOWED,
        resource_type="privacy_request",
        resource_id=request_id,
        detail={"request_type": item.get("request_type"), "status": item.get("status")},
    )

    return {k: item[k] for k in _RESPONSE_FIELDS if k in item}
