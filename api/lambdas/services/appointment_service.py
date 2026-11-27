"""
Appointment service layer for SecureCare backend.

Manages appointments in securecare-appointments:
  PK: appointment_id (String)
  GSIs: ByPatient (patient_id, startsAt), ByDoctor (clinician_id, startsAt), ByDay (day, startsAt)

Concurrency / double-booking prevention:
  Because DynamoDB GSIs are eventually consistent and cannot enforce uniqueness
  constraints by themselves, this service validates slot availability against
  ByDoctor and also performs a deterministic slot lock check / conditional write
  using DynamoDB transactions where appropriate.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from boto3.dynamodb.conditions import Attr, Key

from common.audit import OUTCOME_ALLOWED, log_access, log_denied
from common.auth import AuthContext
from common.authorization import check_permission
from common.cognito import list_group_users
from common.config import get_config
from common.dynamodb import (
    get_item,
    query_all_items,
    query_items,
    serialize_item,
    transact_write_items,
    update_item,
)
from common.exceptions import (
    AppointmentNotFoundError,
    ConflictError,
    DoubleBookingError,
    ForbiddenError,
    InternalError,
    InvalidStateTransitionError,
    PatientNotFoundError,
    ValidationError,
)
from common.identity import resolve_clinician_id, resolve_patient_id
from common.validation import (
    derive_day_from_timestamp,
    parse_iso_to_utc,
    sanitize_allowlisted_fields,
    validate_date,
    validate_iso_timestamp,
    validate_status_transition,
    validate_string,
    validate_visit_type,
)
from models.appointment import Appointment

logger = logging.getLogger("securecare.services.appointment")

_APPOINTMENT_SLOT_MINUTES = 30
_UPDATE_ALLOWLIST = frozenset({"startsAt", "visitType", "status"})


def _normalize_utc_iso(iso_timestamp: str) -> str:
    """Normalize an ISO timestamp to UTC with Z suffix."""
    dt = parse_iso_to_utc(iso_timestamp)
    return dt.isoformat().replace("+00:00", "Z")


def _validate_slot_alignment(iso_timestamp: str) -> None:
    """Require 30-minute UTC boundaries so deterministic IDs serialize slots."""
    slot = parse_iso_to_utc(iso_timestamp)
    if slot.minute not in {0, 30} or slot.second != 0 or slot.microsecond != 0:
        raise ValidationError("startsAt must align to a 30-minute UTC slot")


def _check_double_booking(
    clinician_id: str,
    starts_at_utc: str,
    exclude_appointment_id: str | None = None,
) -> None:
    """Verify no BOOKED appointment for this clinician overlaps within 30 minutes."""
    cfg = get_config()
    target_dt = parse_iso_to_utc(starts_at_utc)
    day = target_dt.strftime("%Y-%m-%d")
    day_start = f"{day}T00:00:00Z"
    day_end = f"{day}T23:59:59.999999Z"

    existing = query_all_items(
        table_name=cfg.APPOINTMENTS_TABLE,
        key_condition=Key("clinician_id").eq(clinician_id) & Key("startsAt").between(day_start, day_end),
        index_name="ByDoctor",
    )

    window = timedelta(minutes=_APPOINTMENT_SLOT_MINUTES)
    for appt in existing:
        if exclude_appointment_id and appt.get("appointment_id") == exclude_appointment_id:
            continue
        if appt.get("status") != "BOOKED":
            continue
        existing_dt = parse_iso_to_utc(appt["startsAt"])
        if abs(target_dt - existing_dt) < window:
            raise DoubleBookingError(
                "Clinician already has a booked appointment in this time slot"
            )


def _verify_clinician_in_directory(clinician_id: str) -> None:
    """Fail closed unless Cognito confirms an enabled doctor account."""
    try:
        doctors = list_group_users("doctor")
    except Exception as exc:  # noqa: BLE001 - directory is advisory at booking
        logger.exception("Clinician directory unavailable")
        raise InternalError("Clinician directory is unavailable") from exc
    doctor_ids = {
        d.get("sub")
        for d in doctors
        if d.get("enabled") is True and d.get("sub")
    }
    if clinician_id not in doctor_ids:
        raise ValidationError("clinician_id is not a registered doctor")


# ---------------------------------------------------------------------------
# Name hydration (UI display names)
# ---------------------------------------------------------------------------

def _enrich_appointments(appointments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach patient_name and clinician_name to each appointment dict."""
    from common.hydration import hydrate_records

    return hydrate_records(appointments)


def create_appointment(auth: AuthContext, data: dict[str, Any]) -> dict[str, Any]:
    """Create a new appointment after role, ownership, and conflict checks."""
    check_permission(auth, "appointment:create")
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object")

    # Determine patient_id securely: patients cannot book on behalf of others
    if auth.is_patient():
        patient_id = resolve_patient_id(auth)
    elif auth.is_staff() or auth.is_admin():
        patient_id = validate_string(data.get("patient_id"), "patient_id", min_length=1, max_length=128) or ""
    else:
        log_denied(auth, "SYSTEM", "CREATE_APPOINTMENT", resource_type="appointment")
        raise ForbiddenError()

    clinician_id = validate_string(data.get("clinician_id"), "clinician_id", min_length=1, max_length=128) or ""
    starts_at_raw = validate_iso_timestamp(data.get("startsAt", ""), "startsAt")
    starts_at = _normalize_utc_iso(starts_at_raw)
    _validate_slot_alignment(starts_at)
    day = derive_day_from_timestamp(starts_at)
    visit_type = validate_visit_type(data.get("visitType", ""))

    cfg = get_config()

    # Verify patient exists
    patient = get_item(cfg.PATIENTS_TABLE, {"patient_id": patient_id})
    if not patient:
        raise PatientNotFoundError("Patient not found")

    # Patients may book with any registered clinician from the directory;
    # a pre-existing securecare-assignments row is NOT required.
    _verify_clinician_in_directory(clinician_id)

    # Double-booking check (30-minute slot window covers exact-timestamp collision)
    _check_double_booking(clinician_id, starts_at)

    # Deterministic slot identity: identical (clinician, startsAt) always
    # maps to the same appointment_id, so concurrent exact-slot bookings
    # collide on the conditional write inside the transaction below.
    slot_key = f"{clinician_id}#{starts_at}"
    appointment_id = str(uuid.uuid5(uuid.NAMESPACE_URL, slot_key))
    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    appt = Appointment(
        appointment_id=appointment_id,
        patient_id=patient_id,
        clinician_id=clinician_id,
        startsAt=starts_at,
        day=day,
        visitType=visit_type,
        status="BOOKED",
        createdBy=auth.sub,
        createdAt=created_at,
    )

    # Atomically create the appointment and upsert the doctor–patient link.
    # The conditional Put guarantees no double-booking for the exact slot;
    # the assignment Put means an appointment can never exist without its
    # corresponding assignment row.
    assignment_item = {
        "clinician_id": clinician_id,
        "patient_id": patient_id,
        "assigned_at": created_at,
    }
    try:
        transact_write_items(
            [
                {
                    "Put": {
                        "TableName": cfg.APPOINTMENTS_TABLE,
                        "Item": serialize_item(appt.to_dynamodb_item()),
                        "ConditionExpression": "attribute_not_exists(appointment_id)",
                    }
                },
                {
                    "Put": {
                        "TableName": cfg.ASSIGNMENTS_TABLE,
                        "Item": serialize_item(assignment_item),
                    }
                },
            ]
        )
    except ConflictError:
        raise DoubleBookingError("Appointment conflict occurred; please retry")

    log_access(
        auth=auth,
        patient_id=patient_id,
        action="CREATE_APPOINTMENT",
        outcome=OUTCOME_ALLOWED,
        resource_type="appointment",
        resource_id=appointment_id,
        detail={"clinician_id": clinician_id, "day": day, "visitType": visit_type},
    )
    response = appt.to_api_response()
    _enrich_appointments([response])
    return response


def get_appointment(auth: AuthContext, appointment_id: str) -> dict[str, Any]:
    """Fetch a single appointment after enforcing role and object-level ownership."""
    check_permission(auth, "appointment:read")
    appointment_id = validate_string(appointment_id, "appointment_id", min_length=1, max_length=128) or ""
    cfg = get_config()

    item = get_item(cfg.APPOINTMENTS_TABLE, {"appointment_id": appointment_id})
    if not item:
        raise AppointmentNotFoundError("Appointment not found")

    appt = Appointment.from_dynamodb_item(item)

    if auth.is_patient():
        if appt.patient_id != resolve_patient_id(auth):
            log_denied(
                auth,
                appt.patient_id,
                "READ_APPOINTMENT",
                resource_type="appointment",
                resource_id=appointment_id,
            )
            raise ForbiddenError()
    elif auth.is_doctor():
        clinician_id = resolve_clinician_id(auth)
        if appt.clinician_id != clinician_id:
            # Check if doctor is assigned to the patient
            assignment = get_item(
                cfg.ASSIGNMENTS_TABLE,
                {"clinician_id": clinician_id, "patient_id": appt.patient_id},
            )
            if not assignment:
                log_denied(
                    auth,
                    appt.patient_id,
                    "READ_APPOINTMENT",
                    resource_type="appointment",
                    resource_id=appointment_id,
                )
                raise ForbiddenError()
    elif not (auth.is_staff() or auth.is_admin()):
        raise ForbiddenError()

    log_access(
        auth=auth,
        patient_id=appt.patient_id,
        action="READ_APPOINTMENT",
        outcome=OUTCOME_ALLOWED,
        resource_type="appointment",
        resource_id=appointment_id,
    )
    response = appt.to_api_response()
    _enrich_appointments([response])
    return response


def list_my_appointments(auth: AuthContext, query_params: dict[str, str]) -> dict[str, Any]:
    """List authorized appointments with scoped, integrity-protected pagination."""
    check_permission(auth, "appointment:list_own")
    cfg = get_config()

    limit = 50
    if "limit" in query_params:
        try:
            limit = max(1, min(100, int(query_params["limit"])))
        except ValueError:
            raise ValidationError("limit must be an integer")

    filter_expr = None

    def add_filter(cond: Any) -> None:
        nonlocal filter_expr
        filter_expr = cond if filter_expr is None else (filter_expr & cond)

    status_val = ""
    if query_params.get("status"):
        status_val = query_params["status"].upper()
        if status_val not in Appointment.VALID_STATUSES:
            raise ValidationError("Invalid status filter")
        add_filter(Attr("status").eq(status_val))

    day_val = validate_date(query_params["day"], "day") if query_params.get("day") else ""
    patient_val = (
        validate_string(query_params.get("patient_id"), "patient_id", min_length=1, max_length=128) or ""
        if query_params.get("patient_id")
        else ""
    )
    clinician_val = (
        validate_string(query_params.get("clinician_id"), "clinician_id", min_length=1, max_length=128) or ""
        if query_params.get("clinician_id")
        else ""
    )

    if auth.is_patient():
        partition_name, partition_value, index = "patient_id", resolve_patient_id(auth), "ByPatient"
        if day_val:
            add_filter(Attr("day").eq(day_val))
        if clinician_val:
            add_filter(Attr("clinician_id").eq(clinician_val))
    elif auth.is_doctor():
        partition_name, partition_value, index = "clinician_id", resolve_clinician_id(auth), "ByDoctor"
        if day_val:
            add_filter(Attr("day").eq(day_val))
        if patient_val:
            add_filter(Attr("patient_id").eq(patient_val))
    elif auth.is_staff() or auth.is_admin():
        if patient_val:
            partition_name, partition_value, index = "patient_id", patient_val, "ByPatient"
            if day_val:
                add_filter(Attr("day").eq(day_val))
            if clinician_val:
                add_filter(Attr("clinician_id").eq(clinician_val))
        elif clinician_val:
            partition_name, partition_value, index = "clinician_id", clinician_val, "ByDoctor"
            if day_val:
                add_filter(Attr("day").eq(day_val))
        elif day_val:
            partition_name, partition_value, index = "day", day_val, "ByDay"
        else:
            raise ValidationError("Provide day, patient_id, or clinician_id to list appointments")
    else:
        raise ForbiddenError()

    from common.cursors import decode_cursor, encode_cursor

    scope = (
        f"appointments:{auth.primary_role}:{auth.sub}:{partition_name}:{partition_value}"
        f":{status_val}:{day_val}:{patient_val}:{clinician_val}"
    )
    start_key = decode_cursor(query_params.get("nextToken") or query_params.get("cursor"), scope)
    result = query_items(
        table_name=cfg.APPOINTMENTS_TABLE,
        key_condition=Key(partition_name).eq(partition_value),
        index_name=index,
        filter_expression=filter_expr,
        limit=limit,
        exclusive_start_key=start_key,
    )

    appointments = [Appointment.from_dynamodb_item(i).to_api_response() for i in result["Items"]]
    _enrich_appointments(appointments)
    log_access(
        auth=auth,
        patient_id=partition_value if partition_name == "patient_id" else "SYSTEM",
        action="LIST_APPOINTMENTS",
        outcome=OUTCOME_ALLOWED,
        resource_type="appointment",
        detail={"count": len(appointments), "scope": partition_name},
    )
    return {
        "appointments": appointments,
        "count": len(appointments),
        "items": appointments,
        "nextToken": encode_cursor(result.get("LastEvaluatedKey"), scope),
    }


def update_appointment(
    auth: AuthContext,
    appointment_id: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Update permitted appointment fields (doctor/staff/admin)."""
    check_permission(auth, "appointment:update")
    appointment_id = validate_string(appointment_id, "appointment_id", min_length=1, max_length=128) or ""

    if not isinstance(updates, dict) or not updates:
        raise ValidationError("At least one updatable field must be provided")

    cfg = get_config()
    existing = get_item(cfg.APPOINTMENTS_TABLE, {"appointment_id": appointment_id})
    if not existing:
        raise AppointmentNotFoundError("Appointment not found")

    appt = Appointment.from_dynamodb_item(existing)

    # Doctors may only update their own appointments
    if auth.is_doctor() and appt.clinician_id != resolve_clinician_id(auth):
        log_denied(
            auth,
            appt.patient_id,
            "UPDATE_APPOINTMENT",
            resource_type="appointment",
            resource_id=appointment_id,
        )
        raise ForbiddenError("Doctors may only update their own appointments")

    # Cannot update terminal appointments
    if appt.status in {"CANCELLED", "COMPLETED"}:
        raise InvalidStateTransitionError(
            f"Cannot modify an appointment with status {appt.status}"
        )

    sanitized = sanitize_allowlisted_fields(updates, set(_UPDATE_ALLOWLIST) | {"status"})
    unexpected = set(updates) - set(_UPDATE_ALLOWLIST)
    if unexpected:
        raise ValidationError(
            "Unsupported appointment fields: " + ", ".join(sorted(unexpected))
        )
    if not sanitized:
        raise ValidationError("No valid updatable fields provided")

    fields_to_set: dict[str, Any] = {}
    if "startsAt" in sanitized:
        starts_at = _normalize_utc_iso(validate_iso_timestamp(sanitized["startsAt"], "startsAt"))
        _validate_slot_alignment(starts_at)
        _check_double_booking(appt.clinician_id, starts_at, exclude_appointment_id=appointment_id)
        fields_to_set["startsAt"] = starts_at
        fields_to_set["day"] = derive_day_from_timestamp(starts_at)

    if "visitType" in sanitized:
        fields_to_set["visitType"] = validate_visit_type(sanitized["visitType"])

    if "status" in sanitized:
        target_status = sanitized["status"]
        try:
            validate_status_transition(appt.status, target_status)
        except ValidationError as exc:
            raise InvalidStateTransitionError(exc.message)
        fields_to_set["status"] = target_status

    # Appointment IDs identify clinician/time slots. When rescheduling, keep
    # the old row as a cancelled record and atomically claim the new
    # deterministic slot ID. This uses only the guide-authorized Put/Update
    # operations and prevents a concurrent booking from claiming the target.
    if "startsAt" in fields_to_set:
        target_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{appt.clinician_id}#{fields_to_set['startsAt']}",
            )
        )
        if target_id != appointment_id:
            target_item = appt.to_dynamodb_item()
            target_item.update(fields_to_set)
            target_item["appointment_id"] = target_id
            try:
                transact_write_items(
                    [
                        {
                            "Put": {
                                "TableName": cfg.APPOINTMENTS_TABLE,
                                "Item": serialize_item(target_item),
                                "ConditionExpression": "attribute_not_exists(appointment_id)",
                            }
                        },
                        {
                            "Update": {
                                "TableName": cfg.APPOINTMENTS_TABLE,
                                "Key": serialize_item({"appointment_id": appointment_id}),
                                "UpdateExpression": "SET #st = :cancelled",
                                "ConditionExpression": "attribute_exists(appointment_id) AND #st = :expected_status",
                                "ExpressionAttributeNames": {"#st": "status"},
                                "ExpressionAttributeValues": {
                                    ":cancelled": {"S": "CANCELLED"},
                                    ":expected_status": {"S": appt.status},
                                },
                            }
                        },
                    ]
                )
            except ConflictError as exc:
                raise DoubleBookingError("Appointment slot is no longer available") from exc

            log_access(
                auth=auth,
                patient_id=appt.patient_id,
                action="UPDATE_APPOINTMENT",
                outcome=OUTCOME_ALLOWED,
                resource_type="appointment",
                resource_id=target_id,
                detail={"updated_fields": sorted(fields_to_set.keys())},
            )
            response = Appointment.from_dynamodb_item(target_item).to_api_response()
            _enrich_appointments([response])
            return response

    expr_parts: list[str] = []
    expr_names: dict[str, str] = {}
    expr_values: dict[str, Any] = {":expected_status": appt.status}

    for idx, (k, v) in enumerate(fields_to_set.items()):
        np = f"#u{idx}"
        vp = f":u{idx}"
        expr_names[np] = k
        expr_values[vp] = v
        expr_parts.append(f"{np} = {vp}")

    expr_names["#cur_status"] = "status"

    updated_item = update_item(
        table_name=cfg.APPOINTMENTS_TABLE,
        key={"appointment_id": appointment_id},
        update_expression="SET " + ", ".join(expr_parts),
        expression_names=expr_names,
        expression_values=expr_values,
        condition_expression="attribute_exists(appointment_id) AND #cur_status = :expected_status",
    )

    log_access(
        auth=auth,
        patient_id=appt.patient_id,
        action="UPDATE_APPOINTMENT",
        outcome=OUTCOME_ALLOWED,
        resource_type="appointment",
        resource_id=appointment_id,
        detail={"updated_fields": sorted(fields_to_set.keys())},
    )
    return Appointment.from_dynamodb_item(updated_item).to_api_response()


def cancel_appointment(auth: AuthContext, appointment_id: str) -> dict[str, Any]:
    """Cancel an eligible BOOKED appointment."""
    appointment_id = validate_string(appointment_id, "appointment_id", min_length=1, max_length=128) or ""
    cfg = get_config()

    existing = get_item(cfg.APPOINTMENTS_TABLE, {"appointment_id": appointment_id})
    if not existing:
        raise AppointmentNotFoundError("Appointment not found")

    appt = Appointment.from_dynamodb_item(existing)

    if auth.is_patient():
        check_permission(auth, "appointment:cancel_own")
        if appt.patient_id != resolve_patient_id(auth):
            log_denied(
                auth,
                appt.patient_id,
                "CANCEL_APPOINTMENT",
                resource_type="appointment",
                resource_id=appointment_id,
            )
            raise ForbiddenError("Patients can only cancel their own appointments")
    elif auth.is_staff() or auth.is_admin():
        check_permission(auth, "appointment:cancel_any")
    else:
        log_denied(
            auth,
            appt.patient_id,
            "CANCEL_APPOINTMENT",
            resource_type="appointment",
            resource_id=appointment_id,
        )
        raise ForbiddenError()

    try:
        validate_status_transition(appt.status, "CANCELLED")
    except ValidationError as exc:
        raise InvalidStateTransitionError(exc.message)

    updated_item = update_item(
        table_name=cfg.APPOINTMENTS_TABLE,
        key={"appointment_id": appointment_id},
        update_expression="SET #st = :cancelled",
        expression_names={"#st": "status"},
        expression_values={":cancelled": "CANCELLED", ":booked": "BOOKED"},
        condition_expression="attribute_exists(appointment_id) AND #st = :booked",
    )

    log_access(
        auth=auth,
        patient_id=appt.patient_id,
        action="CANCEL_APPOINTMENT",
        outcome=OUTCOME_ALLOWED,
        resource_type="appointment",
        resource_id=appointment_id,
    )
    return Appointment.from_dynamodb_item(updated_item).to_api_response()


def list_doctor_appointments(auth: AuthContext) -> list[dict[str, Any]]:
    """List appointments for the authenticated doctor via ByDoctor GSI."""
    check_permission(auth, "appointment:list_by_doctor")
    if not auth.is_doctor():
        log_denied(auth, "SYSTEM", "LIST_DOCTOR_APPOINTMENTS", resource_type="appointment")
        raise ForbiddenError()

    clinician_id = resolve_clinician_id(auth)
    cfg = get_config()
    items = query_all_items(
        table_name=cfg.APPOINTMENTS_TABLE,
        key_condition=Key("clinician_id").eq(clinician_id),
        index_name="ByDoctor",
    )
    appointments = [Appointment.from_dynamodb_item(i).to_api_response() for i in items]
    _enrich_appointments(appointments)
    return appointments


def list_appointments_by_day(auth: AuthContext, day: str) -> list[dict[str, Any]]:
    """List appointments for a specific UTC day (staff/admin only) via ByDay GSI."""
    check_permission(auth, "appointment:list_by_day")
    if not (auth.is_staff() or auth.is_admin()):
        log_denied(auth, "SYSTEM", "LIST_APPOINTMENTS_BY_DAY", resource_type="appointment")
        raise ForbiddenError()

    valid_day = validate_date(day, "day")
    cfg = get_config()
    items = query_all_items(
        table_name=cfg.APPOINTMENTS_TABLE,
        key_condition=Key("day").eq(valid_day),
        index_name="ByDay",
    )

    log_access(
        auth=auth,
        patient_id="SYSTEM",
        action="LIST_APPOINTMENTS_BY_DAY",
        outcome=OUTCOME_ALLOWED,
        resource_type="appointment",
        detail={"day": valid_day, "count": len(items)},
    )
    appointments = [Appointment.from_dynamodb_item(i).to_api_response() for i in items]
    _enrich_appointments(appointments)
    return appointments


def list_clinicians() -> dict[str, Any]:
    """
    Return the doctor directory for the patient booking picker.

    Reads the ``doctor`` Cognito group. Returns a clean list shaped for
    the frontend plus ``items``/``nextToken`` for empty-list safety.
    """
    from common.cognito import derive_display_name

    doctors = list_group_users("doctor")

    clinicians = []
    for doctor in doctors:
        if doctor.get("enabled") is not True:
            continue
        sub = doctor.get("sub")
        if not sub:
            continue
        email = doctor.get("email") or ""
        name = (doctor.get("name") or "").strip() or derive_display_name(email)
        clinicians.append({"clinician_id": sub, "name": name, "email": email})

    return {
        "clinicians": clinicians,
        "count": len(clinicians),
        "items": clinicians,
        "nextToken": None,
    }
