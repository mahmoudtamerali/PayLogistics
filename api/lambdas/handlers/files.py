"""
Lambda handler for File API endpoints.

Endpoints (Schema v3 guide section 8.5/8.6):
  POST /api/files/upload-url               - Request upload URL (201)
  GET  /api/files/{fileId}/download-url    - Download URL for CLEAN files only
  POST /v1/files/upload-url                - Legacy upload route
  POST /v1/files/{file_id}/complete-upload - Complete upload
  GET  /v1/files                           - List files
  GET  /v1/files/{file_id}                 - Get file metadata
  POST /v1/files/{file_id}/download-url    - Legacy download route
  GET  /v1/patients/{patient_id}/files     - List patient files
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import unquote_plus

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

logger = logging.getLogger("securecare.handlers.files")


def _extract_file_id(event: dict[str, Any]) -> str:
    """Read the file id from either the {fileId} or legacy {file_id} path param."""
    params = event.get("pathParameters") or {}
    value = params.get("fileId") or params.get("file_id")
    if not value or not isinstance(value, str) or not value.strip():
        from common.exceptions import BadRequestError

        raise BadRequestError("Missing path parameter: fileId")
    return value.strip()


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Main Lambda handler for file endpoints and S3 scan-verdict notifications."""
    request_id = get_request_id(event)

    try:
        if (
            isinstance(event, dict)
            and isinstance(event.get("Records"), list)
            and any(
                isinstance(rec, dict) and rec.get("eventSource") == "aws:s3"
                for rec in event["Records"]
            )
        ):
            return _handle_s3_scan_events(event)

        method = get_http_method(event)
        resource = get_resource_path(event)

        if method == "OPTIONS":
            return success_response(request_id=request_id, message="OK")

        # Validates Cognito authorizer claims; raises 401 when absent.
        auth = extract_auth_context(event)

        if resource == "/api/files/upload-url" and method == "POST":
            return _request_upload_url(event, auth, request_id)
        elif resource == "/api/files/{fileId}/download-url" and method == "GET":
            return _request_download_url(event, auth, request_id)
        elif resource == "/v1/files/upload-url" and method == "POST":
            return _request_upload_url(event, auth, request_id)
        elif resource == "/v1/files/{file_id}/complete-upload" and method == "POST":
            return _complete_upload(event, auth, request_id)
        elif resource in ("/v1/files", "/api/files") and method == "GET":
            return _list_files(event, auth, request_id)
        elif resource == "/v1/files/{file_id}" and method == "GET":
            return _get_file_metadata(event, auth, request_id)
        elif resource == "/v1/files/{file_id}/download-url" and method == "POST":
            return _request_download_url(event, auth, request_id)
        elif resource == "/v1/patients/{patient_id}/files" and method == "GET":
            return _list_patient_files(event, auth, request_id)
        else:
            return bad_request(f"Unsupported route: {method} {resource}", request_id)

    except SecureCareError as e:
        return error_response(e.status_code, e.error_code, e.message, request_id)
    except Exception:
        logger.exception("Unhandled error in files handler")
        return internal_error(request_id)


def _request_upload_url(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """POST /v1/files/upload-url"""
    from common.authorization import check_permission
    from services.file_service import request_upload_url

    check_permission(auth, "file:request_upload")
    body = parse_request_body(event)
    result = request_upload_url(auth, body)
    return created_response(data=result, request_id=request_id)


def _complete_upload(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """POST /v1/files/{file_id}/complete-upload"""
    from common.authorization import check_permission
    from services.file_service import complete_upload

    check_permission(auth, "file:complete_upload")
    file_id = validate_path_parameter(event, "file_id")
    result = complete_upload(auth, file_id)
    return success_response(data=result, message="Upload completion recorded", request_id=request_id)


def _list_files(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """GET /v1/files"""
    from services.file_service import list_files

    query_params = validate_query_parameters(event, {"patient_id", "status", "limit", "nextToken", "cursor"})
    result = list_files(auth, query_params)
    return success_response(data=result, request_id=request_id)


def _get_file_metadata(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """GET /v1/files/{file_id}"""
    from common.authorization import check_permission
    from services.file_service import get_file_metadata

    check_permission(auth, "file:read_metadata")
    file_id = validate_path_parameter(event, "file_id")
    result = get_file_metadata(auth, file_id)
    return success_response(data=result, request_id=request_id)


def _request_download_url(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """GET /api/files/{fileId}/download-url and legacy POST route."""
    from common.authorization import check_permission
    from services.file_service import request_download_url

    check_permission(auth, "file:download")
    file_id = _extract_file_id(event)
    result = request_download_url(auth, file_id)
    return success_response(data=result, request_id=request_id)


def _list_patient_files(
    event: dict[str, Any], auth: Any, request_id: str
) -> dict[str, Any]:
    """GET /v1/patients/{patient_id}/files"""
    from common.authorization import check_permission
    from services.file_service import list_patient_files

    check_permission(auth, "file:list_patient")
    patient_id = validate_path_parameter(event, "patient_id")
    result = list_patient_files(auth, patient_id)
    return success_response(data={"files": result}, request_id=request_id)


def _handle_s3_scan_events(event: dict[str, Any]) -> dict[str, Any]:
    """Process S3 ObjectCreated events from clean/quarantine verdict buckets."""
    from services.file_service import apply_s3_scan_verdict

    processed = 0
    for rec in event.get("Records", []):
        if not isinstance(rec, dict) or rec.get("eventSource") != "aws:s3":
            continue
        event_name = str(rec.get("eventName") or "")
        if not event_name.startswith("ObjectCreated"):
            continue
        s3_info = rec.get("s3") or {}
        bucket_name = str((s3_info.get("bucket") or {}).get("name") or "")
        raw_key = str((s3_info.get("object") or {}).get("key") or "")
        if not bucket_name or not raw_key:
            continue
        object_key = unquote_plus(raw_key)
        if apply_s3_scan_verdict(bucket_name, object_key) is not None:
            processed += 1

    return {"statusCode": 200, "processed": processed}
