"""Create and look up patient erasure requests.

Requests are persisted as ``PENDING_APPROVAL``. This Lambda does not delete
patient data: the v4 guide says the approval authority and retention rules
are still pending, and a tracking record is not legal approval to erase.
"""

from __future__ import annotations

import logging
from typing import Any

from common.audit import OUTCOME_ALLOWED, log_access, log_denied
from common.auth import AuthContext
from common.authorization import check_permission
from common.exceptions import (
    ConflictError,
    ForbiddenError,
    ServiceUnavailableError,
    ValidationError,
)
from common.identity import resolve_patient_id
from common.privacy_requests import (
    generate_request_id,
    now_iso,
    persist_privacy_request,
)
from common.validation import validate_string

logger = logging.getLogger("securecare.services.erasure")

# Fields accepted from the EraseRequest body model
_CONFIRM_REQUIRED = True
_REASON_MIN = 5
_REASON_MAX = 500


def _validate_erasure_body(data: dict[str, Any] | None) -> str | None:
    """
    Validate the optional EraseRequest body.

    API Gateway enforces the `EraseRequest` model, and this Lambda-side
    check applies the same requirements to direct handler invocations.
    """
    if not isinstance(data, dict) or data.get("confirm") is not _CONFIRM_REQUIRED:
        raise ValidationError("confirm must be true")

    reason = data.get("reason")
    if reason is None:
        raise ValidationError("reason is required")
    reason = validate_string(
        reason, "reason", min_length=_REASON_MIN, max_length=_REASON_MAX
    )
    return reason


def create_erasure_request(
    auth: AuthContext,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Persist a pending request for the authenticated patient. Deletion remains
    disabled until an approved dual-control workflow and retention policy exist.
    """
    check_permission(auth, "erasure:request_own")
    if not auth.is_patient():
        log_denied(auth, "SYSTEM", "CREATE_ERASURE_REQUEST", resource_type="erasure")
        raise ForbiddenError("Only patients can submit an erasure request for their own data")

    reason = _validate_erasure_body(data)

    patient_id = resolve_patient_id(auth)
    request_id = generate_request_id()
    requested_at = now_iso()
    persist_privacy_request(
        patient_id=patient_id,
        request_id=request_id,
        request_type="ERASURE",
        status="PENDING_APPROVAL",
        reason=reason,
        requested_at=requested_at,
    )

    log_access(
        auth=auth,
        patient_id=patient_id,
        action="REQUEST_PATIENT_ERASURE",
        outcome=OUTCOME_ALLOWED,
        resource_type="erasure_request",
        resource_id=request_id,
        detail={"status": "PENDING_APPROVAL"},
    )

    return {
        "request_id": request_id,
        "patient_id": patient_id,
        "request_type": "ERASURE",
        "status": "PENDING_APPROVAL",
        "reason": reason,
        "requested_at": requested_at,
        "note": (
            "Request recorded. No patient data was deleted; approval and "
            "retention policy must be established before execution."
        ),
    }


def get_erasure_status(
    auth: AuthContext,
    request_id: str,
    query_patient_id: str | None = None,
) -> dict[str, Any]:
    """
    Retrieve status of an erasure request from securecare-data-requests.

    Delegates to the shared ownership-checked lookup: patients see only
    their own requests, admins need the patient_id query parameter, and
    doctors/staff receive 403 with a DENIED audit event.
    """
    from common.privacy_requests import resolve_request_lookup

    return resolve_request_lookup(auth, request_id, query_patient_id)


def approve_erasure(auth: AuthContext, request_id: str) -> dict[str, Any]:
    """
    Administrative approval of an erasure request.

    Dual-control approval still requires policy sign-off, so this endpoint
    stays fail-closed.
    """
    check_permission(auth, "erasure:approve")
    if not auth.is_admin():
        log_denied(auth, "SYSTEM", "APPROVE_ERASURE_REQUEST", resource_type="erasure_request", resource_id=request_id)
        raise ForbiddenError("Only admins can approve erasure requests")

    request_id = validate_string(request_id, "request_id", min_length=1, max_length=128) or ""

    log_denied(
        auth,
        "SYSTEM",
        "APPROVE_ERASURE_BLOCKED_NO_WORKFLOW",
        resource_type="erasure_request",
        resource_id=request_id,
    )

    raise ServiceUnavailableError(
        "Erasure approval workflow is disabled: POST /api/erase only records "
        "a pending request, and a "
        "dual-control approval process requires policy confirmation before it "
        "can be enabled."
    )


def execute_erasure(auth: AuthContext, request_id: str) -> dict[str, Any]:
    """
    Execute an approved erasure request (admin workflow endpoint).

    This endpoint remains fail-closed because it must verify a
    persistent approval record and finalized retention policy first.
    Audit records in securecare-audit must never be deleted.
    """
    check_permission(auth, "erasure:execute")
    if not auth.is_admin():
        log_denied(auth, "SYSTEM", "EXECUTE_ERASURE_REQUEST", resource_type="erasure_request", resource_id=request_id)
        raise ForbiddenError("Only admins can execute approved erasure workflows")

    request_id = validate_string(request_id, "request_id", min_length=1, max_length=128) or ""

    log_denied(
        auth,
        "SYSTEM",
        "EXECUTE_ERASURE_BLOCKED",
        resource_type="erasure_request",
        resource_id=request_id,
    )

    raise ConflictError(
        "Destructive erasure execution via this admin endpoint is disabled: it "
        "requires a verified persistent approval record and finalized clinical "
        "retention policy. POST /api/erase only records the request. "
        "Audit records in securecare-audit must never be deleted."
    )
