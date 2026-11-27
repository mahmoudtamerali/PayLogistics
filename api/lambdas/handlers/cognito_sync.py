"""
Cognito Post-Confirmation trigger for SecureCare.

Runs after a user confirms their account in the Cognito User Pool:

1. Adds the new user to the ``patient`` group (never doctor/staff/admin).
2. Creates a baseline ``securecare-patients`` profile row so the patient
   can immediately call ``GET /api/patients/me``.

The handler never raises: a failed group add or profile write is logged
and the original Cognito event is still returned so sign-up succeeds.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from common.cognito import PATIENT_GROUP, add_user_to_group, derive_display_name
from common.config import get_config
from common.dynamodb import put_item
from common.exceptions import ConflictError

logger = logging.getLogger("securecare.handlers.cognito_sync")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Cognito Post-Confirmation trigger entrypoint."""
    try:
        trigger_source = event.get("triggerSource")
        if trigger_source != "PostConfirmation_ConfirmSignUp":
            logger.info(
                "Ignoring non sign-up trigger",
                extra={"triggerSource": trigger_source},
            )
            return event

        attributes = (event.get("request") or {}).get("userAttributes") or {}
        sub = attributes.get("sub")
        username = event.get("userName") or sub
        email = (attributes.get("email") or "").strip()
        name = (attributes.get("name") or "").strip() or derive_display_name(email)
        phone = (attributes.get("phone_number") or "").strip()

        if sub and username:
            _add_patient_group(username)
            _ensure_patient_profile(sub, email, name, phone)
        else:
            logger.warning("Post-Confirmation event missing sub/userName")
    except Exception:  # noqa: BLE001 - never fail the sign-up flow
        logger.exception("Post-Confirmation sync failed (sign-up continues)")

    return event


def _add_patient_group(username: str) -> None:
    """Add the confirmed user to the patient group only."""
    try:
        add_user_to_group(username, PATIENT_GROUP)
        logger.info("Added confirmed user to patient group")
    except Exception:  # noqa: BLE001 - group failure must not block sign-up
        logger.exception("Failed to add confirmed user to patient group")


def _ensure_patient_profile(sub: str, email: str, name: str, phone: str = "") -> None:
    """Create the baseline patient profile row if it does not exist."""
    cfg = get_config()
    item = {
        "patient_id": sub,
        "email": email,
        "fullName": name,
        "phone": phone,
        "createdAt": datetime.now(UTC).isoformat(),
    }
    try:
        put_item(
            cfg.PATIENTS_TABLE,
            item,
            condition_expression="attribute_not_exists(patient_id)",
        )
        logger.info("Created baseline patient profile")
    except ConflictError:
        logger.info("Patient profile already exists, skipping duplicate creation")
    except Exception:  # noqa: BLE001 - profile failure must not block sign-up
        logger.exception("Failed to create baseline patient profile")
