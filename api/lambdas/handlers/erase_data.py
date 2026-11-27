"""
Lambda handler for Data Erasure API endpoints.

Endpoints (Schema v3 guide section 8.5/8.6):
  POST /api/erase                             - Persist a pending erasure request
  GET  /api/requests/{request_id}             - Privacy request status lookup
  POST /v1/patients/me/erasure-requests       - Legacy erasure route
  GET  /v1/erasure-requests/{request_id}      - Legacy status lookup
  POST /v1/admin/erasure-requests/{id}/approve - Admin approval (fail-closed)
  POST /v1/admin/erasure-requests/{id}/execute - Admin execution (fail-closed)

POST /api/erase persists a PENDING_APPROVAL tracking item. Deletion remains
disabled until the approval authority and retention workflow are approved.
"""

from __future__ import annotations

import logging
from typing import Any

from common.auth import extract_auth_context
from common.exceptions import SecureCareError
from common.responses import (
    bad_request,
    created_response,
    error_response,
    internal_error,
    success_response,
)
from common.security import get_http_method, get_request_id, get_resource_path
from common.validation import parse_optional_body, validate_path_parameter, validate_query_parameters

logger = logging.getLogger("securecare.handlers.erase_data")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Main Lambda handler for erasure endpoints."""
    request_id = get_request_id(event)

    try:
        method = get_http_method(event)
        resource = get_resource_path(event)

        if method == "OPTIONS":
            return success_response(request_id=request_id, message="OK")

        # Validates Cognito authorizer claims; raises 401 when absent.
        auth = extract_auth_context(event)

        if resource == "/api/erase" and method == "POST":
            return _create_erasure_request(event, auth, request_id, status_code=200)
        elif resource == "/api/requests/{request_id}" and method == "GET":
            return _get_erasure_status(event, auth, request_id)
        elif resource == "/v1/patients/me/erasure-requests" and method == "POST":
            return _create_erasure_request(event, auth, request_id, status_code=201)
        elif resource == "/v1/erasure-requests/{request_id}" and method == "GET":
            return _get_erasure_status(event, auth, request_id)
        elif resource == "/v1/admin/erasure-requests/{request_id}/approve" and method == "POST":
            return _approve_erasure(event, auth, request_id)
        elif resource == "/v1/admin/erasure-requests/{request_id}/execute" and method == "POST":
            return _execute_erasure(event, auth, request_id)
        else:
            return bad_request(f"Unsupported route: {method} {resource}", request_id)

    except SecureCareError as e:
        return error_response(e.status_code, e.error_code, e.message, request_id)
    except Exception:
        logger.exception("Unhandled error in erase_data handler")
        return internal_error(request_id)


def _create_erasure_request(
    event: dict[str, Any], auth: Any, request_id: str, status_code: int = 200
) -> dict[str, Any]:
    """POST /api/erase and legacy POST /v1/patients/me/erasure-requests."""
    from common.authorization import check_permission
    from services.erasure_service import create_erasure_request

    check_permission(auth, "erasure:request_own")
    body = parse_optional_body(event)
    result = create_erasure_request(auth, body)
    if status_code == 201:
        return created_response(data=result, request_id=request_id)
    # A persisted request returns 200; it is not authorization to delete data.
    return success_response(
        data=result, message="Erasure request recorded", request_id=request_id
    )


def _get_erasure_status(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """
    GET /api/requests/{request_id} and legacy GET /v1/erasure-requests/{request_id}.

    Ownership-checked lookup against securecare-data-requests: patients
    fetch only their own requests, admins must supply patient_id, and
    doctors/staff receive 403 with a DENIED audit event.
    """
    from services.erasure_service import get_erasure_status

    req_id = validate_path_parameter(event, "request_id")
    query = validate_query_parameters(event, {"patient_id"})
    result = get_erasure_status(auth, req_id, query.get("patient_id"))
    return success_response(data=result, request_id=request_id)


def _approve_erasure(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """POST /v1/admin/erasure-requests/{request_id}/approve"""
    from common.authorization import check_permission
    from services.erasure_service import approve_erasure

    check_permission(auth, "erasure:approve")
    req_id = validate_path_parameter(event, "request_id")
    result = approve_erasure(auth, req_id)
    return success_response(data=result, request_id=request_id)


def _execute_erasure(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """POST /v1/admin/erasure-requests/{request_id}/execute"""
    from common.authorization import check_permission
    from services.erasure_service import execute_erasure

    check_permission(auth, "erasure:execute")
    req_id = validate_path_parameter(event, "request_id")
    result = execute_erasure(auth, req_id)
    return success_response(data=result, request_id=request_id)
