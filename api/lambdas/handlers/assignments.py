"""
Lambda handler for Assignment API endpoints.

Endpoints:
  GET    /v1/assignments                             - List assignments
  POST   /v1/assignments                             - Create assignment
  DELETE /v1/assignments/{clinician_id}/{patient_id}  - Remove assignment
  GET    /v1/patients/{patient_id}/doctors            - List patient's doctors
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
    no_content_response,
    success_response,
)
from common.security import get_http_method, get_request_id, get_resource_path
from common.validation import parse_request_body, validate_path_parameter

logger = logging.getLogger("securecare.handlers.assignments")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Main Lambda handler for assignment endpoints."""
    request_id = get_request_id(event)

    try:
        method = get_http_method(event)
        resource = get_resource_path(event)

        if method == "OPTIONS":
            return success_response(request_id=request_id, message="OK")

        auth = extract_auth_context(event)

        if resource in ("/v1/assignments", "/api/assignments") and method == "GET":
            return _list_assignments(event, auth, request_id)
        elif resource in ("/v1/assignments", "/api/assignments") and method == "POST":
            return _create_assignment(event, auth, request_id)
        elif resource == "/api/assignments/{patient_id}" and method == "DELETE":
            return _delete_patient_assignments(event, auth, request_id)
        elif resource == "/v1/assignments/{clinician_id}/{patient_id}" and method == "DELETE":
            return _delete_assignment(event, auth, request_id)
        elif resource == "/v1/patients/{patient_id}/doctors" and method == "GET":
            return _get_patient_doctors(event, auth, request_id)
        else:
            return bad_request(f"Unsupported route: {method} {resource}", request_id)

    except SecureCareError as e:
        return error_response(e.status_code, e.error_code, e.message, request_id)
    except Exception:
        logger.exception("Unhandled error in assignments handler")
        return internal_error(request_id)


def _list_assignments(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """GET /v1/assignments"""
    from common.validation import validate_query_parameters
    from services.assignment_service import list_assignments_page

    params = validate_query_parameters(event, {"clinician_id", "patient_id", "limit", "nextToken"})
    result = list_assignments_page(auth, params)
    return success_response(data=result, request_id=request_id)


def _create_assignment(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """POST /v1/assignments"""
    from common.authorization import check_permission
    from services.assignment_service import create_assignment

    check_permission(auth, "assignment:create")
    body = parse_request_body(event)
    clinician_id = body.get("clinician_id", "")
    patient_id = body.get("patient_id", "")

    if not clinician_id or not patient_id:
        return bad_request("clinician_id and patient_id are required", request_id)

    assignment = create_assignment(auth, clinician_id, patient_id)
    return created_response(data=assignment, request_id=request_id)


def _delete_assignment(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """DELETE /v1/assignments/{clinician_id}/{patient_id}"""
    from common.authorization import check_permission
    from services.assignment_service import delete_assignment

    check_permission(auth, "assignment:delete")
    clinician_id = validate_path_parameter(event, "clinician_id")
    patient_id = validate_path_parameter(event, "patient_id")

    delete_assignment(auth, clinician_id, patient_id)
    return success_response(message="Assignment removed", request_id=request_id)


def _delete_patient_assignments(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """DELETE /api/assignments/{patient_id}; admin may scope by clinician_id."""
    from common.authorization import check_permission
    from common.validation import validate_query_parameters
    from services.assignment_service import delete_patient_assignments

    check_permission(auth, "assignment:delete")
    patient_id = validate_path_parameter(event, "patient_id")
    query = validate_query_parameters(event, {"clinician_id"})
    delete_patient_assignments(auth, patient_id, query.get("clinician_id"))
    return no_content_response(request_id)


def _get_patient_doctors(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """GET /v1/patients/{patient_id}/doctors"""
    from common.authorization import check_permission
    from services.assignment_service import get_patient_doctors

    check_permission(auth, "assignment:list_patient_doctors")
    patient_id = validate_path_parameter(event, "patient_id")
    doctors = get_patient_doctors(auth, patient_id)
    return success_response(data={"doctors": doctors}, request_id=request_id)
