"""
Lambda handler for Audit Log API endpoints.

Endpoints:
  GET /v1/audit                        - Admin audit queries
  GET /v1/audit/by-day/{day}           - Audit by day
  GET /v1/patients/{patient_id}/audit  - Patient-specific audit
"""

from __future__ import annotations

import logging
from typing import Any

from common.auth import extract_auth_context
from common.exceptions import SecureCareError
from common.responses import (
    bad_request,
    error_response,
    internal_error,
    success_response,
)
from common.security import get_http_method, get_request_id, get_resource_path
from common.validation import validate_path_parameter, validate_query_parameters

logger = logging.getLogger("securecare.handlers.audit_log")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Main Lambda handler for audit endpoints."""
    request_id = get_request_id(event)

    try:
        method = get_http_method(event)
        resource = get_resource_path(event)

        if method == "OPTIONS":
            return success_response(request_id=request_id, message="OK")

        auth = extract_auth_context(event)

        if resource in ("/v1/audit", "/api/audit") and method == "GET":
            return _list_audit(event, auth, request_id)
        elif resource == "/v1/audit/by-day/{day}" and method == "GET":
            return _audit_by_day(event, auth, request_id)
        elif resource == "/v1/patients/{patient_id}/audit" and method == "GET":
            return _patient_audit(event, auth, request_id)
        else:
            return bad_request(f"Unsupported route: {method} {resource}", request_id)

    except SecureCareError as e:
        return error_response(e.status_code, e.error_code, e.message, request_id)
    except Exception:
        logger.exception("Unhandled error in audit_log handler")
        return internal_error(request_id)


def _list_audit(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """GET /v1/audit"""
    from services.audit_service import list_admin_audit

    query_params = validate_query_parameters(
        event, {"patient_id", "day", "limit", "cursor", "nextToken"}
    )
    result = list_admin_audit(auth, query_params)
    return success_response(data=result, request_id=request_id)


def _audit_by_day(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """GET /v1/audit/by-day/{day}"""
    from services.audit_service import query_audit_by_day

    day = validate_path_parameter(event, "day")
    query_params = validate_query_parameters(event, {"limit", "cursor", "nextToken"})
    result = query_audit_by_day(auth, day, query_params)
    return success_response(data=result, request_id=request_id)


def _patient_audit(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """GET /v1/patients/{patient_id}/audit"""
    from services.audit_service import query_patient_audit

    patient_id = validate_path_parameter(event, "patient_id")
    query_params = validate_query_parameters(event, {"limit", "cursor", "nextToken"})
    result = query_patient_audit(auth, patient_id, query_params)
    return success_response(data=result, request_id=request_id)
