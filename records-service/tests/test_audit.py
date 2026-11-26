import re
from datetime import datetime, timezone

from app import audit
from app.auth import Caller

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _caller(**kw):
    defaults = dict(caller_id="doc-1", role="doctor", request_id="req-1")
    defaults.update(kw)
    return Caller(**defaults)


def _record_and_capture(monkeypatch, **kw):
    captured = {}
    monkeypatch.setattr(audit.db, "put_audit", captured.update)
    audit.record(_caller(), "pat-1", "read_note", "allowed", **kw)
    return captured


def test_item_has_a_day_field_shaped_yyyy_mm_dd(monkeypatch):
    item = _record_and_capture(monkeypatch)
    assert audit.DAY_ATTR in item
    assert DAY_RE.match(item[audit.DAY_ATTR])


def test_day_is_the_date_of_ts(monkeypatch):
    item = _record_and_capture(monkeypatch)
    ts = datetime.fromisoformat(item["ts"])
    assert item[audit.DAY_ATTR] == ts.strftime("%Y-%m-%d")


def test_event_id_ts_and_day_come_from_the_same_instant(monkeypatch):
    # Pinned right at a day boundary: if ts/event_id/day were computed from
    # separate calls to "now", a boundary crossing between calls would show up here.
    fixed = datetime(2026, 1, 2, 23, 59, 59, 500000, tzinfo=timezone.utc)
    monkeypatch.setattr(audit, "_now", lambda: fixed)
    item = _record_and_capture(monkeypatch)

    assert item["ts"] == fixed.isoformat()
    assert item[audit.DAY_ATTR] == "2026-01-02"
    assert item["event_id"].startswith(f"{fixed:%Y%m%dT%H%M%S%f}Z-")


def test_no_note_text_in_the_audit_item(monkeypatch):
    item = _record_and_capture(monkeypatch, note_id="note-1")
    assert item["note_id"] == "note-1"
    assert "text" not in item
    assert "note_text" not in item
    assert set(item) == {
        "patient_id",
        "event_id",
        "ts",
        audit.DAY_ATTR,
        "caller_id",
        "role",
        "action",
        "outcome",
        "request_id",
        "note_id",
    }
