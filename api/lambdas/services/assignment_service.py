"""
Assignment service layer for SecureCare backend.

Manages doctor-patient assignments in securecare-assignments:
  PK: clinician_id (String)
  SK: patient_id (String)
  GSI ByPatient: PK patient_id, SK clinician_id (Projection: ALL)

Note: The existing schema has no 'active' attribute. Existence of a record
constitutes the active doctor-patient relationship.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from boto3.dynamodb.conditions import Key

from common.audit import OUTCOME_ALLOWED, log_access, log_denied
from common.auth import AuthContext
from common.authorization import check_permission
from common.config import get_config
from common.dynamodb import delete_item, get_item, put_item, query_all_items, query_items
from common.exceptions import (
    AssignmentNotFoundError,
    ConflictError,
    ForbiddenError,
    PatientNotFoundError,
    ValidationError,
)
from common.identity import resolve_clinician_id, resolve_patient_id
from common.validation import validate_string

logger = logging.getLogger("securecare.services.assignment")


def verify_assignment(clinician_id: str, patient_id: str) -> bool:
    """Return True if an assignment record exists for clinician_id and patient_id."""
    cfg = get_config()
    item = get_item(
        cfg.ASSIGNMENTS_TABLE,
        {"clinician_id": clinician_id, "patient_id": patient_id},
    )
    return item is not None


def list_assignments_by_clinician(clinician_id: str) -> list[dict[str, Any]]:
    """Query assignments by clinician_id partition key (hydrated with names)."""
    from common.hydration import hydrate_records

    cfg = get_config()
    assignments = query_all_items(
        table_name=cfg.ASSIGNMENTS_TABLE,
        key_condition=Key("clinician_id").eq(clinician_id),
    )
    return hydrate_records(assignments)


def get_doctor_assignments(auth: AuthContext) -> list[dict[str, Any]]:
    """Return the authenticated doctor's own assignments."""
    check_permission(auth, "assignment:list_own")
    if not auth.is_doctor():
        log_denied(auth, "SYSTEM", "LIST_OWN_ASSIGNMENTS", resource_type="assignment")
        raise ForbiddenError("Only doctors can list their own assignments")

    clinician_id = resolve_clinician_id(auth)
    assignments = list_assignments_by_clinician(clinician_id)
    log_access(
        auth=auth,
        patient_id="SYSTEM",
        action="LIST_OWN_ASSIGNMENTS",
        outcome=OUTCOME_ALLOWED,
        resource_type="assignment",
        detail={"count": len(assignments)},
    )
    return assignments


def list_assignments_page(
    auth: AuthContext, query_params: dict[str, str]
) -> dict[str, Any]:
    """List assignments using a server-scoped, signed DynamoDB cursor."""
    cfg = get_config()
    if auth.is_doctor():
        check_permission(auth, "assignment:list_own")
        partition_name, partition_value, index = "clinician_id", resolve_clinician_id(auth), None
    elif auth.is_admin():
        check_permission(auth, "assignment:list_all")
        if query_params.get("patient_id"):
            partition_name = "patient_id"
            partition_value = validate_string(query_params["patient_id"], "patient_id", min_length=1, max_length=128) or ""
            index = "ByPatient"
        elif query_params.get("clinician_id"):
            partition_name = "clinician_id"
            partition_value = validate_string(query_params["clinician_id"], "clinician_id", min_length=1, max_length=128) or ""
            index = None
        else:
            raise ValidationError("Admin must provide patient_id or clinician_id")
    else:
        raise ForbiddenError()

    try:
        limit = max(1, min(100, int(query_params.get("limit", "50"))))
    except ValueError as exc:
        raise ValidationError("limit must be an integer") from exc
    from common.cursors import decode_cursor, encode_cursor

    scope = f"assignments:{auth.primary_role}:{auth.sub}:{partition_name}:{partition_value}"
    start_key = decode_cursor(query_params.get("nextToken"), scope)
    result = query_items(
        table_name=cfg.ASSIGNMENTS_TABLE,
        key_condition=Key(partition_name).eq(partition_value),
        index_name=index,
        limit=limit,
        exclusive_start_key=start_key,
    )
    from common.hydration import hydrate_records

    assignments = hydrate_records(result["Items"])
    log_access(
        auth=auth,
        patient_id=partition_value if partition_name == "patient_id" else "SYSTEM",
        action="LIST_ASSIGNMENTS",
        outcome=OUTCOME_ALLOWED,
        resource_type="assignment",
        detail={"count": len(assignments), "scope": partition_name},
    )
    return {
        "assignments": assignments,
        "items": assignments,
        "count": len(assignments),
        "nextToken": encode_cursor(result.get("LastEvaluatedKey"), scope),
    }


def create_assignment(
    auth: AuthContext,
    clinician_id: str,
    patient_id: str,
) -> dict[str, Any]:
    """Create a doctor-patient assignment (admin only)."""
    check_permission(auth, "assignment:create")
    if not auth.is_admin():
        log_denied(
            auth,
            patient_id or "SYSTEM",
            "CREATE_ASSIGNMENT",
            resource_type="assignment",
        )
        raise ForbiddenError("Only admins can create assignments")

    clinician_id = validate_string(clinician_id, "clinician_id", min_length=1, max_length=128) or ""
    patient_id = validate_string(patient_id, "patient_id", min_length=1, max_length=128) or ""

    cfg = get_config()

    # Verify patient exists before creating assignment
    patient = get_item(cfg.PATIENTS_TABLE, {"patient_id": patient_id})
    if not patient:
        raise PatientNotFoundError(f"Patient {patient_id} not found")

    assigned_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    item = {
        "clinician_id": clinician_id,
        "patient_id": patient_id,
        "assigned_at": assigned_at,
    }

    try:
        put_item(
            table_name=cfg.ASSIGNMENTS_TABLE,
            item=item,
            condition_expression="attribute_not_exists(clinician_id) AND attribute_not_exists(patient_id)",
        )
    except ConflictError:
        raise ConflictError("Assignment already exists for this clinician and patient")

    log_access(
        auth=auth,
        patient_id=patient_id,
        action="CREATE_ASSIGNMENT",
        outcome=OUTCOME_ALLOWED,
        resource_type="assignment",
        resource_id=f"{clinician_id}#{patient_id}",
        detail={"clinician_id": clinician_id},
    )
    return item


def delete_assignment(
    auth: AuthContext,
    clinician_id: str,
    patient_id: str,
) -> None:
    """Remove a doctor-patient assignment (admin only)."""
    check_permission(auth, "assignment:delete")
    if not auth.is_admin():
        log_denied(
            auth,
            patient_id or "SYSTEM",
            "DELETE_ASSIGNMENT",
            resource_type="assignment",
        )
        raise ForbiddenError("Only admins can remove assignments")

    clinician_id = validate_string(clinician_id, "clinician_id", min_length=1, max_length=128) or ""
    patient_id = validate_string(patient_id, "patient_id", min_length=1, max_length=128) or ""

    cfg = get_config()
    existing = get_item(
        cfg.ASSIGNMENTS_TABLE,
        {"clinician_id": clinician_id, "patient_id": patient_id},
    )
    if not existing:
        raise AssignmentNotFoundError("Assignment not found")

    delete_item(
        table_name=cfg.ASSIGNMENTS_TABLE,
        key={"clinician_id": clinician_id, "patient_id": patient_id},
        condition_expression="attribute_exists(clinician_id) AND attribute_exists(patient_id)",
    )

    log_access(
        auth=auth,
        patient_id=patient_id,
        action="DELETE_ASSIGNMENT",
        outcome=OUTCOME_ALLOWED,
        resource_type="assignment",
        resource_id=f"{clinician_id}#{patient_id}",
        detail={"clinician_id": clinician_id},
    )


def delete_patient_assignments(
    auth: AuthContext, patient_id: str, clinician_id: str | None = None
) -> None:
    """Delete one or all assignments for a patient (admin only)."""
    check_permission(auth, "assignment:delete")
    if not auth.is_admin():
        raise ForbiddenError()
    patient_id = validate_string(patient_id, "patient_id", min_length=1, max_length=128) or ""
    if clinician_id:
        delete_assignment(auth, clinician_id, patient_id)
        return

    cfg = get_config()
    assignments = query_all_items(
        table_name=cfg.ASSIGNMENTS_TABLE,
        key_condition=Key("patient_id").eq(patient_id),
        index_name="ByPatient",
    )
    for assignment in assignments:
        delete_assignment(auth, assignment["clinician_id"], patient_id)


def get_patient_doctors(auth: AuthContext, patient_id: str) -> list[dict[str, Any]]:
    """Return authorized doctors associated with a patient using ByPatient GSI."""
    check_permission(auth, "assignment:list_patient_doctors")
    patient_id = validate_string(patient_id, "patient_id", min_length=1, max_length=128) or ""

    if auth.is_patient():
        own_id = resolve_patient_id(auth)
        if own_id != patient_id:
            log_denied(
                auth,
                patient_id,
                "LIST_PATIENT_DOCTORS",
                resource_type="assignment",
                resource_id=patient_id,
            )
            raise ForbiddenError("Patients may only view their own assigned doctors")
    elif auth.is_doctor():
        clinician_id = resolve_clinician_id(auth)
        if not verify_assignment(clinician_id, patient_id):
            log_denied(
                auth,
                patient_id,
                "LIST_PATIENT_DOCTORS_UNASSIGNED",
                resource_type="assignment",
                resource_id=patient_id,
            )
            raise ForbiddenError("Doctor is not assigned to this patient")
    elif not auth.is_admin():
        log_denied(
            auth,
            patient_id,
            "LIST_PATIENT_DOCTORS",
            resource_type="assignment",
            resource_id=patient_id,
        )
        raise ForbiddenError()

    cfg = get_config()
    doctors = query_all_items(
        table_name=cfg.ASSIGNMENTS_TABLE,
        key_condition=Key("patient_id").eq(patient_id),
        index_name="ByPatient",
    )
    from common.hydration import hydrate_records

    doctors = hydrate_records(doctors)

    log_access(
        auth=auth,
        patient_id=patient_id,
        action="LIST_PATIENT_DOCTORS",
        outcome=OUTCOME_ALLOWED,
        resource_type="assignment",
        resource_id=patient_id,
        detail={"count": len(doctors)},
    )
    return doctors
