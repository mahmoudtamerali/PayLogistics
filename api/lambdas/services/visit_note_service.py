"""
Visit Notes service layer for SecureCare backend.

Manages clinical visit notes in securecare-visit-notes:
  PK: patient_id (String)
  SK: note_id (String) — format: <UTC_ISO_TIMESTAMP>#<UUID4_HEX_12>

Security rules:
  - Clinical notes contain PHI.
  - Patients may read their own notes.
  - Doctors may read and create notes ONLY for assigned patients.
  - Staff CANNOT access clinical notes.
  - Admin CANNOT automatically access clinical notes.
  - Unrestricted editing and deletion are strictly prohibited.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from boto3.dynamodb.conditions import Key

from common.audit import OUTCOME_ALLOWED, log_access, log_denied
from common.auth import AuthContext
from common.authorization import check_permission
from common.config import get_config
from common.dynamodb import get_item, put_item, query_all_items, query_items
from common.exceptions import (
    AssignmentRequiredError,
    ForbiddenError,
    NoteNotFoundError,
    PatientNotFoundError,
    ValidationError,
)
from common.identity import resolve_clinician_id, resolve_patient_id
from common.validation import (
    MAX_NOTE_LENGTH,
    MAX_TITLE_LENGTH,
    validate_string,
)
from models.visit_note import VisitNote

logger = logging.getLogger("securecare.services.visit_note")


def _generate_note_id(now: datetime) -> str:
    """Generate a lexicographically sortable, unique note_id."""
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{ts}#{uuid.uuid4().hex[:12]}"


def _verify_doctor_assignment(clinician_id: str, patient_id: str) -> bool:
    """Verify doctor-patient assignment in securecare-assignments."""
    cfg = get_config()
    item = get_item(
        cfg.ASSIGNMENTS_TABLE,
        {"clinician_id": clinician_id, "patient_id": patient_id},
    )
    return item is not None


def _authorize_note_read(auth: AuthContext, patient_id: str) -> None:
    """Enforce read authorization for a patient's clinical notes."""
    if auth.is_staff() or auth.is_admin():
        log_denied(
            auth,
            patient_id,
            "READ_VISIT_NOTES_ROLE_DENIED",
            resource_type="visit_note",
            resource_id=patient_id,
        )
        raise ForbiddenError(
            "Staff and administrative roles do not have access to clinical notes"
        )

    check_permission(auth, "visit_note:read")

    if auth.is_patient():
        if resolve_patient_id(auth) != patient_id:
            log_denied(
                auth,
                patient_id,
                "READ_VISIT_NOTES_OTHER_PATIENT",
                resource_type="visit_note",
                resource_id=patient_id,
            )
            raise ForbiddenError("Patients can only read their own visit notes")
    elif auth.is_doctor():
        clinician_id = resolve_clinician_id(auth)
        if not _verify_doctor_assignment(clinician_id, patient_id):
            log_denied(
                auth,
                patient_id,
                "READ_VISIT_NOTES_UNASSIGNED",
                resource_type="visit_note",
                resource_id=patient_id,
            )
            raise AssignmentRequiredError("Doctor is not assigned to this patient")
    else:
        raise ForbiddenError()


def create_note(
    auth: AuthContext,
    patient_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Create a clinical visit note for an assigned patient (doctor only)."""
    if auth.is_staff() or auth.is_admin() or auth.is_patient():
        log_denied(
            auth,
            patient_id or "SYSTEM",
            "CREATE_VISIT_NOTE_ROLE_DENIED",
            resource_type="visit_note",
        )
        raise ForbiddenError("Only assigned doctors may create clinical visit notes")

    check_permission(auth, "visit_note:create")
    patient_id = validate_string(patient_id, "patient_id", min_length=1, max_length=128) or ""

    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object")

    # Never accept clinician_id from request body as proof of identity
    clinician_id = resolve_clinician_id(auth)

    if not _verify_doctor_assignment(clinician_id, patient_id):
        log_denied(
            auth,
            patient_id,
            "CREATE_VISIT_NOTE_UNASSIGNED",
            resource_type="visit_note",
            resource_id=patient_id,
        )
        raise AssignmentRequiredError("Doctor is not assigned to this patient")

    cfg = get_config()
    patient = get_item(cfg.PATIENTS_TABLE, {"patient_id": patient_id})
    if not patient:
        raise PatientNotFoundError("Patient not found")

    title = validate_string(
        data.get("title"),
        "title",
        min_length=1,
        max_length=MAX_TITLE_LENGTH,
        required=True,
    )
    note_text = validate_string(
        data.get("note"),
        "note",
        min_length=1,
        max_length=MAX_NOTE_LENGTH,
        required=True,
    )
    if title is None or note_text is None:
        raise ValidationError("title and note are required")

    now = datetime.now(UTC)
    note_id = _generate_note_id(now)
    created_at = now.isoformat().replace("+00:00", "Z")

    visit_note = VisitNote(
        patient_id=patient_id,
        note_id=note_id,
        clinician_id=clinician_id,
        title=title,
        note=note_text,
        created_at=created_at,
    )

    put_item(
        table_name=cfg.VISIT_NOTES_TABLE,
        item=visit_note.to_dynamodb_item(),
        condition_expression="attribute_not_exists(patient_id) AND attribute_not_exists(note_id)",
    )

    # Audit without logging note body (PHI)
    log_access(
        auth=auth,
        patient_id=patient_id,
        action="CREATE_VISIT_NOTE",
        outcome=OUTCOME_ALLOWED,
        resource_type="visit_note",
        resource_id=note_id,
    )

    from common.hydration import hydrate_records

    return hydrate_records([visit_note.to_api_response()])[0]


def list_patient_notes(auth: AuthContext, patient_id: str) -> list[dict[str, Any]]:
    """List visit notes for an authorized patient (newest first)."""
    patient_id = validate_string(patient_id, "patient_id", min_length=1, max_length=128) or ""
    _authorize_note_read(auth, patient_id)

    cfg = get_config()
    items = query_all_items(
        table_name=cfg.VISIT_NOTES_TABLE,
        key_condition=Key("patient_id").eq(patient_id),
        scan_forward=False,
    )

    log_access(
        auth=auth,
        patient_id=patient_id,
        action="LIST_VISIT_NOTES",
        outcome=OUTCOME_ALLOWED,
        resource_type="visit_note",
        detail={"count": len(items)},
    )

    from common.hydration import hydrate_records

    return hydrate_records([VisitNote.from_dynamodb_item(i).to_api_response() for i in items])


def list_patient_notes_page(
    auth: AuthContext, patient_id: str, query_params: dict[str, str]
) -> dict[str, Any]:
    """Return one authorized, newest-first page of clinical notes."""
    patient_id = validate_string(patient_id, "patient_id", min_length=1, max_length=128) or ""
    _authorize_note_read(auth, patient_id)
    try:
        limit = max(1, min(100, int(query_params.get("limit", "50"))))
    except ValueError as exc:
        raise ValidationError("limit must be an integer") from exc
    from common.cursors import decode_cursor, encode_cursor

    scope = f"visit-notes:{auth.primary_role}:{auth.sub}:{patient_id}"
    start_key = decode_cursor(query_params.get("nextToken"), scope)
    cfg = get_config()
    result = query_items(
        table_name=cfg.VISIT_NOTES_TABLE,
        key_condition=Key("patient_id").eq(patient_id),
        scan_forward=False,
        limit=limit,
        exclusive_start_key=start_key,
    )
    from common.hydration import hydrate_records

    notes = hydrate_records([VisitNote.from_dynamodb_item(i).to_api_response() for i in result["Items"]])
    log_access(
        auth=auth,
        patient_id=patient_id,
        action="LIST_VISIT_NOTES",
        outcome=OUTCOME_ALLOWED,
        resource_type="visit_note",
        detail={"count": len(notes)},
    )
    return {
        "notes": notes,
        "items": notes,
        "count": len(notes),
        "nextToken": encode_cursor(result.get("LastEvaluatedKey"), scope),
    }


def get_note(auth: AuthContext, patient_id: str, note_id: str) -> dict[str, Any]:
    """Retrieve a single visit note by composite key (patient_id, note_id)."""
    patient_id = validate_string(patient_id, "patient_id", min_length=1, max_length=128) or ""
    note_id = validate_string(note_id, "note_id", min_length=1, max_length=128) or ""
    _authorize_note_read(auth, patient_id)

    cfg = get_config()
    item = get_item(
        cfg.VISIT_NOTES_TABLE,
        {"patient_id": patient_id, "note_id": note_id},
    )
    if not item:
        raise NoteNotFoundError("Visit note not found")

    log_access(
        auth=auth,
        patient_id=patient_id,
        action="READ_VISIT_NOTE",
        outcome=OUTCOME_ALLOWED,
        resource_type="visit_note",
        resource_id=note_id,
    )

    from common.hydration import hydrate_records

    return hydrate_records([VisitNote.from_dynamodb_item(item).to_api_response()])[0]
