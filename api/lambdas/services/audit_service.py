"""
Audit log query service layer for SecureCare backend.

Queries securecare-audit:
  PK: patient_id (String) — or 'SYSTEM' for system-level events
  SK: event_id (String)
  GSI ByDay: PK day (String), SK event_id (String)

Constraints:
  - Append-only table: no UpdateItem or DeleteItem operations exist.
  - No GSI by actor_id exists; do NOT invent one and do NOT use DynamoDB Scan.
  - Unfiltered system-wide queries are rejected with an explicit explanation.
"""

from __future__ import annotations

import logging
from typing import Any

from boto3.dynamodb.conditions import Key

from common.audit import OUTCOME_ALLOWED, log_access, log_denied
from common.auth import AuthContext
from common.authorization import check_permission
from common.config import get_config
from common.dynamodb import query_items
from common.exceptions import BadRequestError, ForbiddenError, ValidationError
from common.identity import resolve_patient_id
from common.validation import validate_date, validate_string

logger = logging.getLogger("securecare.services.audit")


def _parse_limit(query_params: dict[str, str]) -> int:
    """Parse and clamp pagination limit (default 50, max 100)."""
    raw = query_params.get("limit")
    if not raw:
        return 50
    try:
        val = int(raw)
    except ValueError:
        raise ValidationError("limit must be an integer")
    if val < 1 or val > 100:
        raise ValidationError("limit must be between 1 and 100")
    return val


def query_patient_audit(
    auth: AuthContext,
    patient_id: str,
    query_params: dict[str, str],
) -> dict[str, Any]:
    """Query audit events for a specific patient partition key."""
    patient_id = validate_string(patient_id, "patient_id", min_length=1, max_length=128) or ""

    if auth.is_patient():
        check_permission(auth, "audit:read_own")
        own_id = resolve_patient_id(auth)
        if own_id != patient_id:
            log_denied(
                auth,
                patient_id,
                "READ_PATIENT_AUDIT_OTHER",
                resource_type="audit",
                resource_id=patient_id,
            )
            raise ForbiddenError("Patients may only view their own audit events")
    elif auth.is_admin():
        check_permission(auth, "audit:read_patient")
    else:
        log_denied(
            auth,
            patient_id,
            "READ_PATIENT_AUDIT_ROLE_DENIED",
            resource_type="audit",
            resource_id=patient_id,
        )
        raise ForbiddenError()

    limit = _parse_limit(query_params)
    from common.cursors import decode_cursor, encode_cursor

    scope = f"audit-patient:{auth.primary_role}:{auth.sub}:{patient_id}"
    start_key = decode_cursor(query_params.get("nextToken") or query_params.get("cursor"), scope)

    cfg = get_config()
    result = query_items(
        table_name=cfg.AUDIT_TABLE,
        key_condition=Key("patient_id").eq(patient_id),
        scan_forward=False,
        limit=limit,
        exclusive_start_key=start_key,
    )

    log_access(
        auth=auth,
        patient_id=patient_id,
        action="READ_PATIENT_AUDIT",
        outcome=OUTCOME_ALLOWED,
        resource_type="audit",
        resource_id=patient_id,
        detail={"count": len(result["Items"])},
    )

    return {
        "events": result["Items"],
        "count": len(result["Items"]),
        "next_cursor": encode_cursor(result.get("LastEvaluatedKey"), scope),
        "nextToken": encode_cursor(result.get("LastEvaluatedKey"), scope),
    }


def query_audit_by_day(
    auth: AuthContext,
    day: str,
    query_params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Query audit events for a UTC day using the ByDay GSI (admin only)."""
    check_permission(auth, "audit:read_by_day")
    if not auth.is_admin():
        log_denied(auth, "SYSTEM", "READ_AUDIT_BY_DAY", resource_type="audit")
        raise ForbiddenError("Only admins can query system audit logs by day")

    valid_day = validate_date(day, "day")
    params = query_params or {}
    limit = _parse_limit(params)
    from common.cursors import decode_cursor, encode_cursor

    scope = f"audit-day:{auth.sub}:{valid_day}"
    start_key = decode_cursor(params.get("nextToken") or params.get("cursor"), scope)

    cfg = get_config()
    result = query_items(
        table_name=cfg.AUDIT_TABLE,
        key_condition=Key("day").eq(valid_day),
        index_name="ByDay",
        scan_forward=False,
        limit=limit,
        exclusive_start_key=start_key,
    )

    log_access(
        auth=auth,
        patient_id="SYSTEM",
        action="READ_AUDIT_BY_DAY",
        outcome=OUTCOME_ALLOWED,
        resource_type="audit",
        detail={"day": valid_day, "count": len(result["Items"])},
    )

    return {
        "day": valid_day,
        "events": result["Items"],
        "count": len(result["Items"]),
        "next_cursor": encode_cursor(result.get("LastEvaluatedKey"), scope),
        "nextToken": encode_cursor(result.get("LastEvaluatedKey"), scope),
    }


def list_admin_audit(auth: AuthContext, query_params: dict[str, str]) -> dict[str, Any]:
    """
    Administrative audit query endpoint (GET /v1/audit).

    Requires either 'patient_id' (base table query) or 'day' (ByDay GSI query).
    Unfiltered queries and actor_id queries are rejected because no full-table Scan
    or actor_id GSI is permitted in the schema.
    """
    check_permission(auth, "audit:list_all")
    if not auth.is_admin():
        log_denied(auth, "SYSTEM", "LIST_ADMIN_AUDIT", resource_type="audit")
        raise ForbiddenError("Only admins can query audit logs")

    patient_id = query_params.get("patient_id")
    day = query_params.get("day")

    if patient_id:
        return query_patient_audit(auth, patient_id, query_params)
    if day:
        return query_audit_by_day(auth, day, query_params)

    raise BadRequestError(
        "Audit query requires either 'day' (YYYY-MM-DD) or 'patient_id' filter. "
        "Unfiltered queries and actor_id queries are unsupported without an index (no Scan allowed)."
    )
