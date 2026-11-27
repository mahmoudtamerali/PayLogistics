"""
Lambda handler for Appointment API endpoints.

Endpoints:
  GET    /v1/appointments  /api/appointments    - List appointments
  POST   /v1/appointments  /api/appointments    - Create appointment
  GET    /v1/appointments/{appointment_id}      - Get appointment
  PATCH  /v1/appointments/{appointment_id}      - Update appointment
  POST   /v1/appointments/{appointment_id}/cancel  - Cancel appointment
  GET    /v1/doctors/me/appointments           - Doctor's appointments
  GET    /v1/appointments/by-day/{day}         - Appointments by day
  GET    /api/clinicians                       - Doctor directory
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
from common.validation import (
    parse_request_body,
    validate_path_parameter,
    validate_query_parameters,
)

logger = logging.getLogger("securecare.handlers.appointments")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Main Lambda handler for appointment endpoints."""
    request_id = get_request_id(event)

    try:
        method = get_http_method(event)
        resource = get_resource_path(event)

        if method == "OPTIONS":
            return success_response(request_id=request_id, message="OK")

        auth = extract_auth_context(event)

        if resource in ("/v1/appointments", "/api/appointments") and method == "GET":
            return _list_appointments(event, auth, request_id)
        elif resource in ("/v1/appointments", "/api/appointments") and method == "POST":
            return _create_appointment(event, auth, request_id)
        elif resource == "/api/clinicians" and method == "GET":
            return _list_clinicians(auth, request_id)
        elif resource in ("/v1/appointments/{appointment_id}", "/api/appointments/{appointment_id}") and method == "GET":
            return _get_appointment(event, auth, request_id)
        elif resource in ("/v1/appointments/{appointment_id}", "/api/appointments/{appointment_id}") and method == "PATCH":
            return _update_appointment(event, auth, request_id)
        elif resource == "/api/appointments/{appointment_id}" and method == "DELETE":
            return _cancel_appointment(event, auth, request_id)
        elif resource == "/v1/appointments/{appointment_id}/cancel" and method == "POST":
            return _cancel_appointment(event, auth, request_id)
        elif resource == "/v1/doctors/me/appointments" and method == "GET":
            return _list_doctor_appointments(event, auth, request_id)
        elif resource == "/v1/appointments/by-day/{day}" and method == "GET":
            return _list_by_day(event, auth, request_id)
        else:
            return bad_request(f"Unsupported route: {method} {resource}", request_id)

    except SecureCareError as e:
        return error_response(e.status_code, e.error_code, e.message, request_id)
    except Exception:
        logger.exception("Unhandled error in appointments handler")
        return internal_error(request_id)


def _list_appointments(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """GET /v1/appointments"""
    from common.authorization import check_permission
    from services.appointment_service import list_my_appointments

    check_permission(auth, "appointment:list_own")
    query_params = validate_query_parameters(
        event,
        {"patient_id", "clinician_id", "day", "status", "limit", "nextToken", "cursor"},
    )
    result = list_my_appointments(auth, query_params)
    return success_response(data=result, request_id=request_id)


def _create_appointment(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """POST /v1/appointments"""
    from common.authorization import check_permission
    from services.appointment_service import create_appointment

    check_permission(auth, "appointment:create")
    body = parse_request_body(event)
    appointment = create_appointment(auth, body)
    return created_response(data=appointment, request_id=request_id)


def _get_appointment(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """GET /v1/appointments/{appointment_id}"""
    from common.authorization import check_permission
    from services.appointment_service import get_appointment

    check_permission(auth, "appointment:read")
    appointment_id = validate_path_parameter(event, "appointment_id")
    appointment = get_appointment(auth, appointment_id)
    return success_response(data=appointment, request_id=request_id)


def _update_appointment(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """PATCH /v1/appointments/{appointment_id}"""
    from common.authorization import check_permission
    from services.appointment_service import update_appointment

    check_permission(auth, "appointment:update")
    appointment_id = validate_path_parameter(event, "appointment_id")
    body = parse_request_body(event)
    updated = update_appointment(auth, appointment_id, body)
    return success_response(data=updated, message="Appointment updated", request_id=request_id)


def _cancel_appointment(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """POST /v1/appointments/{appointment_id}/cancel"""
    from services.appointment_service import cancel_appointment

    # Authorization check happens inside cancel_appointment based on role
    appointment_id = validate_path_parameter(event, "appointment_id")
    result = cancel_appointment(auth, appointment_id)
    return success_response(data=result, message="Appointment cancelled", request_id=request_id)


def _list_doctor_appointments(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """GET /v1/doctors/me/appointments"""
    from common.authorization import check_permission
    from services.appointment_service import list_doctor_appointments

    check_permission(auth, "appointment:list_by_doctor")
    appointments = list_doctor_appointments(auth)
    return success_response(
        data={
            "appointments": appointments,
            "count": len(appointments),
            "items": appointments,
            "nextToken": None,
        },
        request_id=request_id,
    )


def _list_by_day(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """GET /v1/appointments/by-day/{day}"""
    from common.authorization import check_permission
    from services.appointment_service import list_appointments_by_day

    check_permission(auth, "appointment:list_by_day")
    day = validate_path_parameter(event, "day")
    appointments = list_appointments_by_day(auth, day)
    return success_response(
        data={
            "appointments": appointments,
            "count": len(appointments),
            "items": appointments,
            "nextToken": None,
        },
        request_id=request_id,
    )


def _list_clinicians(auth: Any, request_id: str) -> dict[str, Any]:
    """GET /api/clinicians - doctor directory for patient booking."""
    from common.authorization import check_permission
    from services.appointment_service import list_clinicians

    check_permission(auth, "clinician:list")
    result = list_clinicians()
    return success_response(data=result, request_id=request_id)
