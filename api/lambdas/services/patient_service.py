"""
Patient service layer for SecureCare backend.

Enforces role-based and object-level access control for patient profiles.
Uses securecare-patients and securecare-assignments tables.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from common.audit import OUTCOME_ALLOWED, log_access, log_denied
from common.auth import AuthContext
from common.authorization import check_permission
from common.config import get_config
from common.dynamodb import get_item, update_item
from common.exceptions import (
    AssignmentRequiredError,
    ForbiddenError,
    PatientNotFoundError,
    ValidationError,
)
from common.identity import resolve_clinician_id, resolve_patient_id
from common.security import validate_no_script_injection
from common.validation import (
    MAX_FULLNAME_LENGTH,
    sanitize_allowlisted_fields,
    validate_email,
    validate_phone,
    validate_string,
)
from models.patient import Patient

logger = logging.getLogger("securecare.services.patient")

# Only email, phone, and fullName may be updated by a patient via PATCH /v1/patients/me.
# patient_id, dateOfBirth, createdAt, role, and group fields are strictly immutable.
_PATIENT_PATCH_ALLOWLIST = frozenset({"email", "phone", "fullName"})


def get_patient(patient_id: str) -> dict[str, Any]:
    """Fetch a raw patient record by ID or raise PatientNotFoundError."""
    cfg = get_config()
    item = get_item(cfg.PATIENTS_TABLE, {"patient_id": patient_id})
    if not item:
        raise PatientNotFoundError("Patient not found")
    return item


def verify_patient_exists(patient_id: str) -> bool:
    """Return True if a patient record exists in DynamoDB."""
    cfg = get_config()
    item = get_item(
        cfg.PATIENTS_TABLE,
        {"patient_id": patient_id},
        projection="patient_id",
    )
    return item is not None


def get_my_profile(auth: AuthContext) -> dict[str, Any]:
    """Return the authenticated patient's own profile."""
    check_permission(auth, "patient:read_own")
    if not auth.is_patient():
        log_denied(auth, auth.sub, "READ_OWN_PROFILE", resource_type="patient")
        raise ForbiddenError("Only patients can access /patients/me")

    patient_id = resolve_patient_id(auth)
    cfg = get_config()
    item = get_item(cfg.PATIENTS_TABLE, {"patient_id": patient_id})
    if not item:
        # Self-registration: the Post-Confirmation trigger may not have run
        # (or the row was removed) – auto-provision a baseline profile from
        # the verified JWT claims so the patient is never blocked.
        item = _auto_provision_profile(auth, patient_id)
    patient = Patient.from_dynamodb_item(item)
    return patient.to_api_response(self_access=True)


def _auto_provision_profile(auth: AuthContext, patient_id: str) -> dict[str, Any]:
    """Create a baseline patient row from JWT claims. Returns the stored item."""
    from common.cognito import derive_display_name
    from common.dynamodb import put_item
    from common.exceptions import ConflictError

    cfg = get_config()
    full_name = auth.name or derive_display_name(auth.email)
    item = {
        "patient_id": patient_id,
        "email": auth.email or "",
        "fullName": full_name,
        "phone": auth.phone or "",
        "createdAt": datetime.now(UTC).isoformat(),
    }
    try:
        put_item(
            cfg.PATIENTS_TABLE,
            item,
            condition_expression="attribute_not_exists(patient_id)",
        )
        logger.info("Auto-provisioned patient profile")
    except ConflictError:
        # Raced with a concurrent writer – re-read the stored row.
        item = get_item(cfg.PATIENTS_TABLE, {"patient_id": patient_id}) or item
    return item


def update_my_profile(auth: AuthContext, updates: dict[str, Any]) -> dict[str, Any]:
    """Update allowlisted profile fields for the authenticated patient."""
    check_permission(auth, "patient:update_own")
    if not auth.is_patient():
        log_denied(auth, auth.sub, "UPDATE_OWN_PROFILE", resource_type="patient")
        raise ForbiddenError("Only patients can update /patients/me")

    if not isinstance(updates, dict) or not updates:
        raise ValidationError("At least one updatable field must be provided")

    # Reject attempts to tamper with protected identity/role attributes explicitly
    forbidden_keys = {"patient_id", "role", "roles", "groups", "cognito:groups", "createdAt", "dateOfBirth"}
    attempted_forbidden = forbidden_keys.intersection(updates.keys())
    if attempted_forbidden:
        log_denied(
            auth,
            auth.sub,
            "UPDATE_OWN_PROFILE_FORBIDDEN_FIELDS",
            resource_type="patient",
            detail={"fields": sorted(attempted_forbidden)},
        )
        raise ValidationError(
            f"Cannot modify protected fields: {', '.join(sorted(attempted_forbidden))}"
        )

    sanitized = sanitize_allowlisted_fields(updates, set(_PATIENT_PATCH_ALLOWLIST))
    if not sanitized:
        raise ValidationError(
            f"No valid updatable fields provided. Allowed: {', '.join(sorted(_PATIENT_PATCH_ALLOWLIST))}"
        )

    validated: dict[str, Any] = {}
    if "email" in sanitized:
        validated["email"] = validate_email(sanitized["email"])
    if "phone" in sanitized:
        validated["phone"] = validate_phone(sanitized["phone"])
    if "fullName" in sanitized:
        name = validate_string(
            sanitized["fullName"],
            "fullName",
            min_length=1,
            max_length=MAX_FULLNAME_LENGTH,
            required=True,
        )
        if name is None:
            raise ValidationError("fullName is required")
        validated["fullName"] = validate_no_script_injection(name, "fullName")

    patient_id = resolve_patient_id(auth)
    cfg = get_config()

    expr_parts: list[str] = []
    expr_values: dict[str, Any] = {}
    expr_names: dict[str, str] = {}

    for idx, (field, value) in enumerate(validated.items()):
        name_placeholder = f"#f{idx}"
        val_placeholder = f":v{idx}"
        expr_names[name_placeholder] = field
        expr_values[val_placeholder] = value
        expr_parts.append(f"{name_placeholder} = {val_placeholder}")

    updated_item = update_item(
        table_name=cfg.PATIENTS_TABLE,
        key={"patient_id": patient_id},
        update_expression="SET " + ", ".join(expr_parts),
        expression_values=expr_values,
        expression_names=expr_names,
        condition_expression="attribute_exists(patient_id)",
    )

    log_access(
        auth=auth,
        patient_id=patient_id,
        action="UPDATE_OWN_PROFILE",
        outcome=OUTCOME_ALLOWED,
        resource_type="patient",
        resource_id=patient_id,
        detail={"updated_fields": sorted(validated.keys())},
    )

    patient = Patient.from_dynamodb_item(updated_item)
    return patient.to_api_response(self_access=True)


def get_patient_for_authorized_user(auth: AuthContext, patient_id: str) -> dict[str, Any]:
    """Return permitted patient information after enforcing role and relationship checks."""
    check_permission(auth, "patient:read_by_id")
    patient_id = validate_string(patient_id, "patient_id", min_length=1, max_length=128) or ""
    cfg = get_config()

    if auth.is_doctor():
        clinician_id = resolve_clinician_id(auth)
        assignment = get_item(
            cfg.ASSIGNMENTS_TABLE,
            {"clinician_id": clinician_id, "patient_id": patient_id},
        )
        if not assignment:
            log_denied(
                auth,
                patient_id,
                "READ_PATIENT_PROFILE_UNASSIGNED",
                resource_type="patient",
                resource_id=patient_id,
            )
            raise AssignmentRequiredError(
                "Doctor is not assigned to this patient"
            )
        item = get_patient(patient_id)
        patient = Patient.from_dynamodb_item(item)
        return patient.to_api_response(self_access=False, include_contact=True)

    if auth.is_staff() or auth.is_admin():
        item = get_patient(patient_id)
        patient = Patient.from_dynamodb_item(item)
        # Operational view: omit sensitive contact details unless needed
        return patient.to_api_response(self_access=False, include_contact=False)

    log_denied(
        auth,
        patient_id,
        "READ_PATIENT_PROFILE",
        resource_type="patient",
        resource_id=patient_id,
    )
    raise ForbiddenError()
