"""Audit trail: who did what to whose record, and was it allowed.

Rule (fail closed): the audit entry for an ALLOWED action is written BEFORE the
action happens. If the audit write fails, the action does not happen and the
caller gets 503. Entries never contain note text.
"""
import uuid
from datetime import datetime, timezone

from . import db, obs
from .auth import ApiError, Caller

# Name of the day-bucket attribute (hash key for the ByDay GSI; event_id is the range key).
# Kept as a single constant so it is a one-line change if the name picked differs.
DAY_ATTR = "day"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_event_id(now: datetime = None) -> str:
    now = now or _now()
    return f"{now:%Y%m%dT%H%M%S%f}Z-{uuid.uuid4().hex[:12]}"


def record(caller: Caller, patient_id: str, action: str, outcome: str, note_id: str = "") -> None:
    """Write one audit entry. Raises ApiError(503) if it cannot be written
    and the outcome is 'allowed'; for 'denied' it only reports the failure."""
    now = _now()
    item = {
        "patient_id": patient_id,
        "event_id": new_event_id(now),
        "ts": now.isoformat(),
        DAY_ATTR: now.strftime("%Y-%m-%d"),
        "caller_id": caller.caller_id,
        "role": caller.role,
        "action": action,
        "outcome": outcome,
        "request_id": caller.request_id,
    }
    if note_id:
        item["note_id"] = note_id
    try:
        db.put_audit(item)
    except Exception as err:  # noqa: BLE001 - any failure must be handled the same way
        obs.metric("AuditWriteFailures", action=action, outcome=outcome, request_id=caller.request_id)
        obs.log("audit_write_failed", error=type(err).__name__, request_id=caller.request_id)
        if outcome == "allowed":
            raise ApiError(503, "audit_unavailable")
