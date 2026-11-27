"""
Lambda handler for Patient API endpoints.

Endpoints:
  GET  /v1/patients/me            - Get own profile
  PATCH /v1/patients/me           - Update own profile
  GET  /v1/patients/{patient_id}  - Get patient by ID (authorized)
  GET  /v1/patients/{patient_id}/audit - Get patient audit events
"""

from __future__ import annotations

import logging
from typing import Any

from common.audit import log_access
from common.auth import extract_auth_context
from common.exceptions import SecureCareError
from common.responses import (
    bad_request,
    error_response,
    internal_error,
    success_response,
)
from common.security import get_http_method, get_request_id, get_resource_path
from common.validation import parse_request_body, validate_path_parameter

logger = logging.getLogger("securecare.handlers.patients")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Main Lambda handler for patient endpoints."""
    request_id = get_request_id(event)

    try:
        method = get_http_method(event)
        resource = get_resource_path(event)

        # OPTIONS for CORS preflight
        if method == "OPTIONS":
            return success_response(request_id=request_id, message="OK")

        # Extract authenticated identity
        auth = extract_auth_context(event)

        # Route to appropriate handler
        if resource in ("/v1/patients/me", "/api/patients/me") and method == "GET":
            return _get_my_profile(event, auth, request_id)
        elif resource in ("/v1/patients/me", "/api/patients/me") and method == "PATCH":
            return _update_my_profile(event, auth, request_id)
        elif resource in ("/v1/patients/{patient_id}", "/api/patients/{patient_id}") and method == "GET":
            return _get_patient_by_id(event, auth, request_id)
        elif resource == "/v1/patients/{patient_id}/audit" and method == "GET":
            return _get_patient_audit(event, auth, request_id)
        else:
            return bad_request(f"Unsupported route: {method} {resource}", request_id)

    except SecureCareError as e:
        return error_response(e.status_code, e.error_code, e.message, request_id)
    except Exception:
        logger.exception("Unhandled error in patients handler")
        return internal_error(request_id)


def _get_my_profile(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """GET /v1/patients/me"""
    from common.authorization import check_permission
    from services.patient_service import get_my_profile

    check_permission(auth, "patient:read_own")
    patient = get_my_profile(auth)
    log_access(auth, auth.sub, "READ_OWN_PROFILE", resource_type="patient")
    return success_response(data=patient, request_id=request_id)


def _update_my_profile(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """PATCH /v1/patients/me"""
    from common.authorization import check_permission
    from services.patient_service import update_my_profile

    check_permission(auth, "patient:update_own")
    body = parse_request_body(event)
    updated = update_my_profile(auth, body)
    return success_response(data=updated, message="Profile updated successfully", request_id=request_id)


def _get_patient_by_id(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """GET /v1/patients/{patient_id}"""
    from common.authorization import check_permission
    from services.patient_service import get_patient_for_authorized_user

    check_permission(auth, "patient:read_by_id")
    patient_id = validate_path_parameter(event, "patient_id")
    patient = get_patient_for_authorized_user(auth, patient_id)
    log_access(
        auth, patient_id, "READ_PATIENT_PROFILE",
        resource_type="patient", resource_id=patient_id,
    )
    return success_response(data=patient, request_id=request_id)


def _get_patient_audit(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """GET /v1/patients/{patient_id}/audit"""
    from common.validation import validate_query_parameters
    from services.audit_service import query_patient_audit

    patient_id = validate_path_parameter(event, "patient_id")
    query_params = validate_query_parameters(event, {"limit", "cursor"})
    result = query_patient_audit(auth, patient_id, query_params)
    return success_response(data=result, request_id=request_id)
