"""
Lambda handler for Visit Notes API endpoints.

Endpoints:
  GET  /v1/patients/{patient_id}/visit-notes             - List notes
  POST /v1/patients/{patient_id}/visit-notes             - Create note
  GET  /v1/patients/{patient_id}/visit-notes/{note_id}   - Get note
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
from common.validation import parse_request_body, validate_path_parameter

logger = logging.getLogger("securecare.handlers.visit_notes")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Main Lambda handler for visit notes endpoints."""
    request_id = get_request_id(event)

    try:
        method = get_http_method(event)
        resource = get_resource_path(event)

        if method == "OPTIONS":
            return success_response(request_id=request_id, message="OK")

        auth = extract_auth_context(event)

        if resource in ("/v1/patients/{patient_id}/visit-notes", "/api/visit-notes/{patient_id}") and method == "GET":
            return _list_notes(event, auth, request_id)
        elif resource == "/api/visit-notes" and method == "GET":
            return _list_notes(event, auth, request_id, api_collection=True)
        elif resource in ("/v1/patients/{patient_id}/visit-notes", "/api/visit-notes/{patient_id}") and method == "POST":
            return _create_note(event, auth, request_id)
        elif resource in ("/v1/patients/{patient_id}/visit-notes/{note_id}", "/api/visit-notes/{patient_id}/{note_id}") and method == "GET":
            return _get_note(event, auth, request_id)
        else:
            return bad_request(f"Unsupported route: {method} {resource}", request_id)

    except SecureCareError as e:
        return error_response(e.status_code, e.error_code, e.message, request_id)
    except Exception:
        logger.exception("Unhandled error in visit_notes handler")
        return internal_error(request_id)


def _list_notes(
    event: dict[str, Any], auth: Any, request_id: str, api_collection: bool = False
) -> dict[str, Any]:
    """GET /api/visit-notes or the legacy patient-scoped route."""
    from common.validation import validate_query_parameters
    from services.visit_note_service import list_patient_notes, list_patient_notes_page

    if api_collection:
        params = validate_query_parameters(event, {"patient_id", "limit", "nextToken"})
        patient_id = params.get("patient_id")
        if auth.is_patient():
            patient_id = auth.sub
        if not patient_id:
            from common.exceptions import ValidationError
            raise ValidationError("patient_id is required for doctor note queries")
        result = list_patient_notes_page(auth, patient_id, params)
        return success_response(data=result, request_id=request_id)

    patient_id = validate_path_parameter(event, "patient_id")
    notes = list_patient_notes(auth, patient_id)
    return success_response(data={"notes": notes, "items": notes, "nextToken": None}, request_id=request_id)


def _create_note(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """POST /v1/patients/{patient_id}/visit-notes"""
    from services.visit_note_service import create_note

    patient_id = validate_path_parameter(event, "patient_id")
    body = parse_request_body(event)
    note = create_note(auth, patient_id, body)
    return created_response(data=note, request_id=request_id)


def _get_note(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """GET /v1/patients/{patient_id}/visit-notes/{note_id}"""
    from services.visit_note_service import get_note

    patient_id = validate_path_parameter(event, "patient_id")
    note_id = validate_path_parameter(event, "note_id")
    note = get_note(auth, patient_id, note_id)
    return success_response(data=note, request_id=request_id)
