"""
Lambda handler for Data Export API endpoints.

Endpoints (Schema v3 guide section 8.5/8.6):
  GET  /api/export                  - Export authenticated patient's own data
  GET  /api/requests/{request_id}   - Privacy request status lookup
  POST /v1/patients/me/export       - Legacy export route (same behaviour)
  GET  /v1/exports/{export_id}      - Async export status (fails closed)
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
from common.validation import validate_path_parameter, validate_query_parameters

logger = logging.getLogger("securecare.handlers.export_data")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Main Lambda handler for export and privacy-request endpoints."""
    request_id = get_request_id(event)

    try:
        method = get_http_method(event)
        resource = get_resource_path(event)

        if method == "OPTIONS":
            return success_response(request_id=request_id, message="OK")

        # Validates Cognito authorizer claims; raises 401 when absent.
        auth = extract_auth_context(event)

        if resource == "/api/export" and method == "GET":
            return _create_export(event, auth, request_id, status_code=200)
        elif resource == "/api/requests/{request_id}" and method == "GET":
            return _get_request_status(event, auth, request_id)
        elif resource == "/v1/patients/me/export" and method == "POST":
            return _create_export(event, auth, request_id, status_code=201)
        elif resource == "/v1/exports/{export_id}" and method == "GET":
            return _get_export_status(event, auth, request_id)
        else:
            return bad_request(f"Unsupported route: {method} {resource}", request_id)

    except SecureCareError as e:
        return error_response(e.status_code, e.error_code, e.message, request_id)
    except Exception:
        logger.exception("Unhandled error in export_data handler")
        return internal_error(request_id)


def _create_export(
    event: dict[str, Any], auth: Any, request_id: str, status_code: int = 200
) -> dict[str, Any]:
    """GET /api/export and legacy POST /v1/patients/me/export."""
    from common.authorization import check_permission
    from services.export_service import create_export

    check_permission(auth, "export:request_own")

    # Optional format parameter – only json is supported initially.
    query = validate_query_parameters(event, {"format"})
    fmt = query.get("format")
    if fmt and fmt.lower() != "json":
        return bad_request("Only format=json is supported", request_id)

    result = create_export(auth)
    if status_code == 201:
        return created_response(data=result, request_id=request_id)
    # Synchronous demo execution returns 200 (guide: 200, not 202, because
    # no asynchronous SQS worker is deployed).
    return success_response(
        data=result, message="Export completed", request_id=request_id
    )


def _get_request_status(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """GET /api/requests/{request_id} – ownership-checked status lookup."""
    from services.export_service import get_request_status

    req_id = validate_path_parameter(event, "request_id")
    query = validate_query_parameters(event, {"patient_id"})
    result = get_request_status(auth, req_id, query.get("patient_id"))
    return success_response(data=result, request_id=request_id)


def _get_export_status(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """GET /v1/exports/{export_id}"""
    from common.authorization import check_permission
    from services.export_service import get_export_status

    check_permission(auth, "export:check_status")
    export_id = validate_path_parameter(event, "export_id")
    result = get_export_status(auth, export_id)
    return success_response(data=result, request_id=request_id)
