"""
File service layer for SecureCare backend.

Implements a fail-closed, scan-gated file upload and download workflow:
  1. Authenticated user requests an upload URL (POST /v1/files/upload-url).
  2. Backend validates role, ownership, filename, contentType, sizeBytes.
  3. Backend generates a unique safe objectKey and pre-signed PUT URL to UPLOAD_BUCKET.
  4. Metadata is recorded in securecare-files with status='PENDING_SCAN'.
  5. Complete-upload confirms object presence and keeps status='PENDING_SCAN'.
  6. Download URLs are strictly blocked for PENDING_SCAN and QUARANTINED files.
  7. Only a trusted backend scanner integration (not an application role) may
     transition a file to CLEAN or QUARANTINED.

IMPORTANT: Malware scanning is NOT yet deployed in AWS. All new uploads remain
PENDING_SCAN and cannot be downloaded until a real scanning workflow is deployed.
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

from boto3.dynamodb.conditions import Attr, Key

from common.audit import OUTCOME_ALLOWED, log_access, log_denied, log_system_event
from common.auth import AuthContext
from common.authorization import check_permission
from common.config import get_config
from common.dynamodb import get_item, put_item, query_all_items, query_items, update_item
from common.exceptions import (
    AssignmentRequiredError,
    ConflictError,
    FileNotFoundError_,
    FileQuarantinedError,
    FileScanPendingError,
    ForbiddenError,
    PatientNotFoundError,
    ValidationError,
)
from common.identity import resolve_clinician_id, resolve_patient_id
from common.s3_storage import (
    check_object_exists,
    generate_object_key,
    generate_presigned_download_url,
    generate_presigned_upload_url,
    get_object_metadata,
)
from common.validation import (
    validate_file_content_type,
    validate_file_name,
    validate_file_size,
    validate_string,
)
from models.file import FileRecord

logger = logging.getLogger("securecare.services.file")


class MalwareScannerInterface(ABC):
    """
    Abstract integration interface for a future backend malware scanner.

    NOTE: This interface is NOT exposed via API Gateway and has no
    application user role. A real scanning worker (e.g., AWS Lambda
    triggered by S3 ObjectCreated using ClamAV or GuardDuty Malware
    Protection) must implement this contract using an isolated IAM role.
    """

    @abstractmethod
    def scan_object(self, bucket: str, object_key: str, expected_content_type: str) -> str:
        """Scan an S3 object and return 'CLEAN' or 'QUARANTINED'."""
        raise NotImplementedError


class UnconfiguredMalwareScanner(MalwareScannerInterface):
    """Fail-closed placeholder indicating malware scanning is not yet operational."""

    def scan_object(self, bucket: str, object_key: str, expected_content_type: str) -> str:
        raise NotImplementedError(
            "Malware scanning engine is not deployed. Files remain PENDING_SCAN."
        )


def _hydrate_file_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach patient_name display field onto serialized file records."""
    if not records:
        return records
    from common.hydration import PATIENT_NAME_FALLBACK, resolve_patient_names

    names = resolve_patient_names({r.get("patient_id") for r in records})
    for r in records:
        r["patient_name"] = names.get(r.get("patient_id"), PATIENT_NAME_FALLBACK)
    return records


def _authorize_patient_file_access(
    auth: AuthContext,
    patient_id: str,
    *,
    allow_staff_admin: bool = True,
) -> None:
    """Enforce object-level authorization for a patient's files."""
    cfg = get_config()
    if auth.is_patient():
        if resolve_patient_id(auth) != patient_id:
            log_denied(
                auth,
                patient_id,
                "FILE_ACCESS_OTHER_PATIENT",
                resource_type="file",
                resource_id=patient_id,
            )
            raise ForbiddenError("Patients may only access their own files")
    elif auth.is_doctor():
        clinician_id = resolve_clinician_id(auth)
        assignment = get_item(
            cfg.ASSIGNMENTS_TABLE,
            {"clinician_id": clinician_id, "patient_id": patient_id},
        )
        if not assignment:
            log_denied(
                auth,
                patient_id,
                "FILE_ACCESS_UNASSIGNED",
                resource_type="file",
                resource_id=patient_id,
            )
            raise AssignmentRequiredError("Doctor is not assigned to this patient")
    elif auth.is_staff() or auth.is_admin():
        if not allow_staff_admin:
            raise ForbiddenError("Role is not authorized for this file operation")
    else:
        raise ForbiddenError()


def request_upload_url(auth: AuthContext, data: dict[str, Any]) -> dict[str, Any]:
    """Validate metadata, record PENDING_SCAN file metadata, and issue a pre-signed PUT URL."""
    check_permission(auth, "file:request_upload")
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object")

    if auth.is_patient():
        patient_id = resolve_patient_id(auth)
    elif auth.is_doctor() or auth.is_staff():
        patient_id = validate_string(data.get("patient_id"), "patient_id", min_length=1, max_length=128) or ""
        _authorize_patient_file_access(auth, patient_id, allow_staff_admin=True)
    else:
        log_denied(auth, "SYSTEM", "REQUEST_UPLOAD_URL", resource_type="file")
        raise ForbiddenError()

    cfg = get_config()
    patient = get_item(cfg.PATIENTS_TABLE, {"patient_id": patient_id})
    if not patient:
        raise PatientNotFoundError("Patient not found")

    file_name = validate_file_name(data.get("fileName", ""))
    content_type = validate_file_content_type(data.get("contentType", ""))
    size_bytes = validate_file_size(data.get("sizeBytes"))

    file_id = str(uuid.uuid4())
    # Never trust client-supplied objectKey; generate uniquely server-side
    # under patients/{patient_id}/{fileId}/{sanitized_fileName}
    object_key = generate_object_key(patient_id, file_id, file_name, content_type)
    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    record = FileRecord(
        fileId=file_id,
        patient_id=patient_id,
        objectKey=object_key,
        fileName=file_name,
        contentType=content_type,
        sizeBytes=size_bytes,
        status="PENDING_SCAN",
        uploadedBy=auth.sub,
        createdAt=created_at,
    )

    put_item(
        table_name=cfg.FILES_TABLE,
        item=record.to_dynamodb_item(),
        condition_expression="attribute_not_exists(fileId)",
    )

    presigned = generate_presigned_upload_url(
        bucket=cfg.UPLOAD_BUCKET,
        object_key=object_key,
        content_type=content_type,
        max_size_bytes=cfg.MAX_FILE_SIZE_BYTES,
        expiry_seconds=cfg.PRESIGNED_URL_EXPIRY,
    )

    log_access(
        auth=auth,
        patient_id=patient_id,
        action="REQUEST_FILE_UPLOAD_URL",
        outcome=OUTCOME_ALLOWED,
        resource_type="file",
        resource_id=file_id,
        detail={"contentType": content_type, "sizeBytes": size_bytes},
    )

    # Return pre-signed URL without exposing objectKey unnecessarily
    return {
        "fileId": file_id,
        "status": "PENDING_SCAN",
        "upload_url": presigned["upload_url"],
        "method": presigned["method"],
        "headers": presigned["headers"],
        "expires_in_seconds": presigned["expires_in_seconds"],
    }


def complete_upload(auth: AuthContext, file_id: str) -> dict[str, Any]:
    """Confirm upload completion in S3 while keeping status strictly at PENDING_SCAN."""
    check_permission(auth, "file:complete_upload")
    file_id = validate_string(file_id, "file_id", min_length=1, max_length=128) or ""

    cfg = get_config()
    item = get_item(cfg.FILES_TABLE, {"fileId": file_id})
    if not item:
        raise FileNotFoundError_("File not found")

    record = FileRecord.from_dynamodb_item(item)

    # Only the original uploader (or authorized staff/admin on their behalf? Spec: verify uploader matches auth.sub)
    if record.uploadedBy != auth.sub:
        log_denied(
            auth,
            record.patient_id,
            "COMPLETE_FILE_UPLOAD_NOT_UPLOADER",
            resource_type="file",
            resource_id=file_id,
        )
        raise ForbiddenError("Only the initiating uploader may complete the upload")

    if record.status != "PENDING_SCAN":
        raise ValidationError(f"File is already in status {record.status}")

    # Verify the object was actually uploaded to the private upload bucket
    s3_meta = get_object_metadata(cfg.UPLOAD_BUCKET, record.objectKey)
    if not s3_meta:
        raise ValidationError("Uploaded file not found in upload bucket")

    actual_size = int(s3_meta.get("content_length", 0))
    if actual_size <= 0 or actual_size > cfg.MAX_FILE_SIZE_BYTES:
        raise ValidationError("Uploaded object size violates policy limits")

    # Never transition status to CLEAN here; files remain PENDING_SCAN until scanned
    log_access(
        auth=auth,
        patient_id=record.patient_id,
        action="COMPLETE_FILE_UPLOAD",
        outcome=OUTCOME_ALLOWED,
        resource_type="file",
        resource_id=file_id,
        detail={"actualSizeBytes": actual_size, "status": "PENDING_SCAN"},
    )
    return _hydrate_file_records([record.to_api_response()])[0]


def list_files(auth: AuthContext, query_params: dict[str, str]) -> dict[str, Any]:
    """List files accessible to the authenticated user."""
    if auth.is_patient():
        check_permission(auth, "file:list_own")
        patient_id = resolve_patient_id(auth)
    else:
        check_permission(auth, "file:list_patient")
        patient_id = query_params.get("patient_id", "")
        if not patient_id:
            raise ValidationError(
                "patient_id query parameter is required to avoid unindexed full-table scan"
            )
        _authorize_patient_file_access(auth, patient_id, allow_staff_admin=True)

    status_filter = None
    if query_params.get("status"):
        st = query_params["status"].upper()
        if st not in FileRecord.VALID_STATUSES:
            raise ValidationError("Invalid file status filter")
        status_filter = Attr("status").eq(st)

    try:
        limit = max(1, min(100, int(query_params.get("limit", "50"))))
    except ValueError as exc:
        raise ValidationError("limit must be an integer") from exc
    from common.cursors import decode_cursor, encode_cursor

    scope = f"files:{auth.primary_role}:{auth.sub}:{patient_id}:{query_params.get('status', '').upper()}"
    start_key = decode_cursor(query_params.get("nextToken") or query_params.get("cursor"), scope)
    cfg = get_config()
    result = query_items(
        table_name=cfg.FILES_TABLE,
        key_condition=Key("patient_id").eq(patient_id),
        index_name="ByPatient",
        filter_expression=status_filter,
        scan_forward=False,
        limit=limit,
        exclusive_start_key=start_key,
    )

    files = _hydrate_file_records([FileRecord.from_dynamodb_item(i).to_api_response() for i in result["Items"]])
    log_access(
        auth=auth,
        patient_id=patient_id,
        action="LIST_FILES",
        outcome=OUTCOME_ALLOWED,
        resource_type="file",
        detail={"count": len(files)},
    )
    return {
        "files": files,
        "items": files,
        "count": len(files),
        "nextToken": encode_cursor(result.get("LastEvaluatedKey"), scope),
    }


def list_patient_files(auth: AuthContext, patient_id: str) -> list[dict[str, Any]]:
    """List files for a specific authorized patient via ByPatient GSI."""
    check_permission(auth, "file:list_patient")
    patient_id = validate_string(patient_id, "patient_id", min_length=1, max_length=128) or ""
    _authorize_patient_file_access(auth, patient_id, allow_staff_admin=True)

    cfg = get_config()
    items = query_all_items(
        table_name=cfg.FILES_TABLE,
        key_condition=Key("patient_id").eq(patient_id),
        index_name="ByPatient",
        scan_forward=False,
    )

    log_access(
        auth=auth,
        patient_id=patient_id,
        action="LIST_PATIENT_FILES",
        outcome=OUTCOME_ALLOWED,
        resource_type="file",
        detail={"count": len(items)},
    )
    return _hydrate_file_records([FileRecord.from_dynamodb_item(i).to_api_response() for i in items])


def get_file_metadata(auth: AuthContext, file_id: str) -> dict[str, Any]:
    """Return file metadata (without objectKey) after authorization."""
    check_permission(auth, "file:read_metadata")
    file_id = validate_string(file_id, "file_id", min_length=1, max_length=128) or ""

    cfg = get_config()
    item = get_item(cfg.FILES_TABLE, {"fileId": file_id})
    if not item:
        raise FileNotFoundError_("File not found")

    record = FileRecord.from_dynamodb_item(item)
    _authorize_patient_file_access(auth, record.patient_id, allow_staff_admin=True)

    log_access(
        auth=auth,
        patient_id=record.patient_id,
        action="READ_FILE_METADATA",
        outcome=OUTCOME_ALLOWED,
        resource_type="file",
        resource_id=file_id,
    )
    return _hydrate_file_records([record.to_api_response()])[0]


def request_download_url(auth: AuthContext, file_id: str) -> dict[str, Any]:
    """Generate a short-lived download URL ONLY when file status is CLEAN."""
    check_permission(auth, "file:download")
    if not (auth.is_patient() or auth.is_doctor()):
        log_denied(auth, "SYSTEM", "REQUEST_FILE_DOWNLOAD_ROLE_DENIED", resource_type="file", resource_id=file_id)
        raise ForbiddenError("Only patients and assigned doctors may download files")

    file_id = validate_string(file_id, "file_id", min_length=1, max_length=128) or ""

    cfg = get_config()
    item = get_item(cfg.FILES_TABLE, {"fileId": file_id})
    if not item:
        raise FileNotFoundError_("File not found")

    record = FileRecord.from_dynamodb_item(item)
    _authorize_patient_file_access(auth, record.patient_id, allow_staff_admin=False)

    # Strictly fail-closed on scan state
    if record.status == "PENDING_SCAN":
        log_denied(
            auth,
            record.patient_id,
            "DOWNLOAD_PENDING_SCAN_BLOCKED",
            resource_type="file",
            resource_id=file_id,
        )
        raise FileScanPendingError("File is pending malware scan and cannot be downloaded")

    if record.status == "QUARANTINED":
        log_denied(
            auth,
            record.patient_id,
            "DOWNLOAD_QUARANTINED_BLOCKED",
            resource_type="file",
            resource_id=file_id,
        )
        raise FileQuarantinedError("File has been quarantined and cannot be downloaded")

    if record.status != "CLEAN":
        raise ForbiddenError("File is not cleared for download")

    download_url = generate_presigned_download_url(
        bucket=cfg.CLEAN_BUCKET,
        object_key=record.objectKey,
        expiry_seconds=cfg.DOWNLOAD_URL_TTL,
        response_content_type=record.contentType,
        response_filename=record.fileName,
    )

    log_access(
        auth=auth,
        patient_id=record.patient_id,
        action="REQUEST_FILE_DOWNLOAD_URL",
        outcome=OUTCOME_ALLOWED,
        resource_type="file",
        resource_id=file_id,
    )

    return {
        "fileId": file_id,
        "fileName": record.fileName,
        "contentType": record.contentType,
        "download_url": download_url,
        "expires_in_seconds": cfg.DOWNLOAD_URL_TTL,
    }


def transition_scan_status_internal(
    file_id: str,
    new_status: str,
    scanner: MalwareScannerInterface,
) -> dict[str, Any]:
    """
    Internal-only backend transition for trusted malware-scanning workflows.

    NOT exposed to any API Gateway endpoint or application user role.
    Requires an explicit MalwareScannerInterface instance.
    """
    if new_status not in {"CLEAN", "QUARANTINED"}:
        raise ValidationError("Scanner transition status must be CLEAN or QUARANTINED")

    cfg = get_config()
    item = get_item(cfg.FILES_TABLE, {"fileId": file_id})
    if not item:
        raise FileNotFoundError_("File not found")

    record = FileRecord.from_dynamodb_item(item)
    # Invoke scanner contract (raises NotImplementedError if UnconfiguredMalwareScanner)
    verdict = scanner.scan_object(cfg.UPLOAD_BUCKET, record.objectKey, record.contentType)
    if verdict != new_status:
        raise ValidationError("Scanner verdict mismatch")

    updated = update_item(
        table_name=cfg.FILES_TABLE,
        key={"fileId": file_id},
        update_expression="SET #st = :new_status",
        expression_names={"#st": "status"},
        expression_values={":new_status": new_status, ":pending": "PENDING_SCAN"},
        condition_expression="attribute_exists(fileId) AND #st = :pending",
    )

    log_system_event(
        action=f"FILE_SCAN_{new_status}",
        outcome=OUTCOME_ALLOWED,
        resource_type="file",
        resource_id=file_id,
        detail={"patient_id": record.patient_id},
    )
    return FileRecord.from_dynamodb_item(updated).to_api_response()


class _S3VerdictScanner(MalwareScannerInterface):
    """MalwareScannerInterface backed by an S3 ObjectCreated verdict bucket event."""

    def __init__(self, expected_status: str, verdict_bucket: str) -> None:
        self._expected_status = expected_status
        self._verdict_bucket = verdict_bucket

    def scan_object(self, bucket: str, object_key: str, expected_content_type: str) -> str:
        if self._expected_status == "CLEAN" and not check_object_exists(self._verdict_bucket, object_key):
            raise ValidationError("Scanned object is missing from the clean bucket")
        return self._expected_status


def apply_s3_scan_verdict(bucket_name: str, object_key: str) -> dict[str, Any] | None:
    """
    Transition a file record when the ClamAV worker lands it in clean/quarantine.

    Expected object_key layout: ``patients/{patient_id}/{fileId}/{filename}``.
    Returns the updated API dict, or None when the object is unrecognized or already settled.
    """
    cfg = get_config()
    if bucket_name == cfg.CLEAN_BUCKET:
        new_status = "CLEAN"
    elif bucket_name == cfg.QUARANTINE_BUCKET:
        new_status = "QUARANTINED"
    else:
        logger.warning("Ignoring S3 scan event for unrecognized bucket: %s", bucket_name)
        return None

    parts = [p for p in (object_key or "").split("/") if p]
    if len(parts) < 4 or parts[0] != "patients":
        logger.info("Ignoring non-patient S3 object key: %s", object_key)
        return None

    patient_id, file_id = parts[1], parts[2]
    item = get_item(cfg.FILES_TABLE, {"fileId": file_id})
    if not item:
        logger.warning("No file metadata found for S3 scan object: fileId=%s", file_id)
        return None

    record = FileRecord.from_dynamodb_item(item)
    if record.patient_id != patient_id or record.objectKey != object_key:
        logger.error(
            "S3 key mismatch for fileId=%s (expected patient/key did not match event)",
            file_id,
        )
        return None

    if record.status == new_status:
        logger.info("File %s already in status %s (idempotent)", file_id, new_status)
        return record.to_api_response()

    if record.status != "PENDING_SCAN":
        logger.warning(
            "Skipping scan transition for file %s in unexpected status %s -> %s",
            file_id,
            record.status,
            new_status,
        )
        return None

    try:
        return transition_scan_status_internal(
            file_id=file_id,
            new_status=new_status,
            scanner=_S3VerdictScanner(new_status, bucket_name),
        )
    except ConflictError:
        logger.info("Concurrent scan transition already completed for file %s", file_id)
        return None
