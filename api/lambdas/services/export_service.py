"""
Data Export service layer for SecureCare backend.

Flow (Schema v3 / guide section 8.6):
  - GET /api/export (and legacy POST /v1/patients/me/export) performs a
    bounded synchronous export of the authenticated patient's own data and
    persists a tracking item (request_type=EXPORT, status=COMPLETED) in the
    securecare-data-requests table. The response always includes
    request_id and status.
  - GET /api/requests/{request_id} performs an ownership-checked lookup of
    the persisted tracking item (see common/privacy_requests.py).
  - GET /v1/exports/{export_id} fails closed with 404: asynchronous export
    jobs are not part of the synchronous demo flow.
"""

from __future__ import annotations

import logging
from typing import Any

from boto3.dynamodb.conditions import Key

from common.audit import OUTCOME_ALLOWED, log_access, log_denied
from common.auth import AuthContext
from common.authorization import check_permission
from common.config import get_config
from common.dynamodb import get_item, query_all_items
from common.exceptions import ForbiddenError, NotFoundError, PatientNotFoundError
from common.identity import resolve_patient_id
from common.privacy_requests import (
    generate_request_id,
    now_iso,
    persist_privacy_request,
    resolve_request_lookup,
)
from common.validation import validate_string
from models.appointment import Appointment
from models.file import FileRecord
from models.patient import Patient
from models.visit_note import VisitNote

logger = logging.getLogger("securecare.services.export")


def create_export(auth: AuthContext) -> dict[str, Any]:
    """
    Synchronously export the authenticated patient's own authorized data.

    Only patients may export their own data. Staff, doctors, and admins are denied.
    """
    check_permission(auth, "export:request_own")
    if not auth.is_patient():
        log_denied(auth, "SYSTEM", "EXPORT_PATIENT_DATA", resource_type="export")
        raise ForbiddenError("Only patients can request their own data export")

    patient_id = resolve_patient_id(auth)
    cfg = get_config()

    patient_item = get_item(cfg.PATIENTS_TABLE, {"patient_id": patient_id})
    if not patient_item:
        raise PatientNotFoundError("Patient profile not found")

    profile = Patient.from_dynamodb_item(patient_item).to_api_response(self_access=True)

    appointments_raw = query_all_items(
        table_name=cfg.APPOINTMENTS_TABLE,
        key_condition=Key("patient_id").eq(patient_id),
        index_name="ByPatient",
    )
    appointments = [Appointment.from_dynamodb_item(i).to_api_response() for i in appointments_raw]

    notes_raw = query_all_items(
        table_name=cfg.VISIT_NOTES_TABLE,
        key_condition=Key("patient_id").eq(patient_id),
        scan_forward=False,
    )
    visit_notes = [VisitNote.from_dynamodb_item(i).to_api_response() for i in notes_raw]

    files_raw = query_all_items(
        table_name=cfg.FILES_TABLE,
        key_condition=Key("patient_id").eq(patient_id),
        index_name="ByPatient",
    )
    # Never expose internal S3 objectKey in exported metadata
    files = [FileRecord.from_dynamodb_item(i).to_api_response() for i in files_raw]

    # Durable privacy-request tracking (securecare-data-requests):
    # request_id = <UTC ISO timestamp>#<UUID>, status=COMPLETED for this
    # synchronous demo flow.
    export_id = generate_request_id()
    exported_at = now_iso()
    persist_privacy_request(
        patient_id=patient_id,
        request_id=export_id,
        request_type="EXPORT",
        status="COMPLETED",
        requested_at=exported_at,
        completed_at=exported_at,
    )

    log_access(
        auth=auth,
        patient_id=patient_id,
        action="EXPORT_PATIENT_DATA",
        outcome=OUTCOME_ALLOWED,
        resource_type="export",
        resource_id=export_id,
        detail={
            "appointments_count": len(appointments),
            "visit_notes_count": len(visit_notes),
            "files_count": len(files),
        },
    )

    return {
        "request_id": export_id,
        "export_id": export_id,
        "status": "COMPLETED",
        "mode": "SYNCHRONOUS",
        "exported_at": exported_at,
        "bundle": {
            "patient": profile,
            "appointments": appointments,
            "visit_notes": visit_notes,
            "files": files,
        },
    }


def get_request_status(
    auth: AuthContext,
    request_id: str,
    query_patient_id: str | None = None,
) -> dict[str, Any]:
    """
    GET /api/requests/{request_id} – ownership-checked status lookup.

    Patients fetch only their own requests; admins fetch any patient's
    request when the patient_id query parameter is supplied; doctors and
    staff are denied with 403 and a DENIED audit event.
    """
    return resolve_request_lookup(auth, request_id, query_patient_id)


def get_export_status(auth: AuthContext, export_id: str) -> dict[str, Any]:
    """
    Retrieve status of an asynchronous export job.

    The synchronous demo flow has no export-job table: durable EXPORT
    tracking records live in securecare-data-requests and are read via
    GET /api/requests/{request_id}. export_id is not a request_id, so this
    endpoint fails closed without fabricating stored job state.
    """
    check_permission(auth, "export:check_status")
    export_id = validate_string(export_id, "export_id", min_length=1, max_length=128) or ""

    log_denied(
        auth,
        auth.sub if auth.is_patient() else "SYSTEM",
        "GET_EXPORT_STATUS_UNSUPPORTED",
        resource_type="export",
        resource_id=export_id,
    )

    raise NotFoundError(
        "Asynchronous export jobs are not tracked. Use GET /api/requests/{request_id} "
        "to look up a persisted EXPORT request from the synchronous export flow."
    )
