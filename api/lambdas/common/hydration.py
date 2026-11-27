"""
Display-name hydration helpers shared by list endpoints.

Resolves patient ``fullName`` via a single BatchGetItem and clinician
names via Cognito (cached). Both are best-effort: failures degrade to
sensible fallback labels rather than breaking a response.
"""

from __future__ import annotations

import logging
from typing import Any

from common.cognito import get_user_display_name
from common.config import get_config
from common.dynamodb import batch_get_items

logger = logging.getLogger("securecare.hydration")

PATIENT_NAME_FALLBACK = "Unknown Patient"
CLINICIAN_NAME_FALLBACK = "Unknown Clinician"


def resolve_patient_names(patient_ids: set[str]) -> dict[str, str]:
    """Batch-fetch ``fullName`` for a set of patient ids."""
    ids = sorted(pid for pid in patient_ids if pid)
    if not ids:
        return {}
    cfg = get_config()
    try:
        items = batch_get_items(
            cfg.PATIENTS_TABLE,
            [{"patient_id": pid} for pid in ids],
            projection="patient_id, fullName",
        )
    except Exception:  # noqa: BLE001 - hydration must not break responses
        logger.exception("patient_name batch lookup failed")
        return {}
    return {
        item["patient_id"]: item.get("fullName") or PATIENT_NAME_FALLBACK
        for item in items
        if item.get("patient_id")
    }


def resolve_clinician_names(clinician_ids: set[str]) -> dict[str, str]:
    """Resolve clinician display names via Cognito (cached per container)."""
    names: dict[str, str] = {}
    for cid in sorted(c for c in clinician_ids if c):
        names[cid] = get_user_display_name(cid) or CLINICIAN_NAME_FALLBACK
    return names


def hydrate_records(
    records: list[dict[str, Any]],
    patient_key: str = "patient_id",
    clinician_key: str = "clinician_id",
) -> list[dict[str, Any]]:
    """Attach ``patient_name`` and ``clinician_name`` to each record in place."""
    if not records:
        return records
    patient_names = resolve_patient_names(
        {record.get(patient_key) for record in records}
    )
    clinician_names = resolve_clinician_names(
        {record.get(clinician_key) for record in records}
    )
    for record in records:
        record["patient_name"] = patient_names.get(
            record.get(patient_key), PATIENT_NAME_FALLBACK
        )
        record["clinician_name"] = clinician_names.get(
            record.get(clinician_key), CLINICIAN_NAME_FALLBACK
        )
    return records
