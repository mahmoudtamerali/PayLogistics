"""
Standardised HTTP response builders for API Gateway Lambda Proxy Integration.

All API responses use a consistent JSON envelope:

Success:
{
    "success": true,
    "data": { ... },
    "message": "...",
    "request_id": "..."
}

Error:
{
    "success": false,
    "error": {
        "code": "ERROR_CODE",
        "message": "Human-readable message"
    },
    "request_id": "..."
}
"""

import json
import os
from typing import Any

# CORS headers – the allowed origin is read from ALLOWED_ORIGIN at call
# time so tests and deployments can override it per environment.
def _cors_headers() -> dict[str, str]:
    origin = os.environ.get("ALLOWED_ORIGIN", "")
    headers = {
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "GET,POST,PATCH,DELETE,OPTIONS",
        "Access-Control-Max-Age": "86400",
        "Content-Type": "application/json",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    }
    # Local development explicitly sets ALLOWED_ORIGIN. In a same-origin
    # CloudFront deployment, omitting it disables cross-origin CORS headers.
    if origin:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Vary"] = "Origin"
    else:
        headers.pop("Access-Control-Allow-Headers", None)
        headers.pop("Access-Control-Allow-Methods", None)
        headers.pop("Access-Control-Max-Age", None)
    return headers


def _build_response(
    status_code: int,
    body: dict[str, Any],
    request_id: str | None = None,
) -> dict[str, Any]:
    """Build an API Gateway proxy integration response."""
    if request_id:
        body["request_id"] = request_id
    headers = _cors_headers()
    if request_id:
        headers["X-Request-Id"] = request_id
    return {
        "statusCode": status_code,
        "headers": headers,
        "body": json.dumps(body, default=str),
    }


def success_response(
    data: Any = None,
    message: str = "Operation completed successfully",
    status_code: int = 200,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Return a successful API response."""
    body: dict[str, Any] = {
        "success": True,
        "data": data if data is not None else {},
        "message": message,
    }
    return _build_response(status_code, body, request_id)


def created_response(
    data: Any = None,
    message: str = "Resource created successfully",
    request_id: str | None = None,
) -> dict[str, Any]:
    """Return a 201 Created response."""
    return success_response(data=data, message=message, status_code=201, request_id=request_id)


def no_content_response(request_id: str | None = None) -> dict[str, Any]:
    """Return a 204 No Content response."""
    headers = _cors_headers()
    if request_id:
        headers["X-Request-Id"] = request_id
    return {
        "statusCode": 204,
        "headers": headers,
        "body": "",
    }


def error_response(
    status_code: int,
    error_code: str,
    message: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Return a standardised error response."""
    body: dict[str, Any] = {
        "success": False,
        "error": {
            "code": error_code,
            "message": message,
        },
    }
    return _build_response(status_code, body, request_id)


def bad_request(message: str = "Invalid request", request_id: str | None = None) -> dict[str, Any]:
    return error_response(400, "BAD_REQUEST", message, request_id)


def unauthorized(message: str = "Authentication required", request_id: str | None = None) -> dict[str, Any]:
    return error_response(401, "UNAUTHENTICATED", message, request_id)


def forbidden(message: str = "Forbidden", request_id: str | None = None) -> dict[str, Any]:
    return error_response(403, "FORBIDDEN", message, request_id)


def not_found(message: str = "Resource not found", request_id: str | None = None) -> dict[str, Any]:
    return error_response(404, "NOT_FOUND", message, request_id)


def conflict(message: str = "Resource conflict", request_id: str | None = None) -> dict[str, Any]:
    return error_response(409, "CONFLICT", message, request_id)


def internal_error(request_id: str | None = None) -> dict[str, Any]:
    return error_response(500, "INTERNAL_ERROR", "An internal error occurred", request_id)
