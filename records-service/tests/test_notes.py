import json

import boto3
from boto3.dynamodb.conditions import Key

from app import config, db

from .conftest import TOKEN, headers

NOTE = {"visit_date": "2026-11-09", "text": "Synthetic visit note. Follow up in two weeks."}


def audit_items(patient_id="pat-1"):
    t = boto3.resource("dynamodb", region_name="eu-west-1").Table(config.AUDIT_TABLE)
    return t.query(KeyConditionExpression=Key("patient_id").eq(patient_id))["Items"]


def create(client, **kw):
    return client.post("/patients/pat-1/notes", json=kw.pop("body", NOTE), headers=kw.pop("h", headers()))


# ----- happy path -----

def test_assigned_doctor_can_create_and_read(client, aws):
    r = create(client)
    assert r.status_code == 201
    note = r.json()
    assert note["author_id"] == "doc-1" and note["patient_id"] == "pat-1"
    assert note["text"] == NOTE["text"]

    r = client.get(f"/patients/pat-1/notes/{note['note_id']}", headers=headers())
    assert r.status_code == 200 and r.json() == note

    r = client.get("/patients/pat-1/notes", headers=headers("nurse-1", "nurse"))
    assert r.status_code == 200
    assert [n["note_id"] for n in r.json()["notes"]] == [note["note_id"]]


def test_list_is_newest_first_and_limited(client, aws):
    ids = [create(client).json()["note_id"] for _ in range(3)]
    r = client.get("/patients/pat-1/notes?limit=2", headers=headers())
    assert [n["note_id"] for n in r.json()["notes"]] == ids[::-1][:2]
    assert client.get("/patients/pat-1/notes?limit=0", headers=headers()).status_code == 422
    assert client.get("/patients/pat-1/notes?limit=101", headers=headers()).status_code == 422


def test_patient_reads_only_own_notes(client, aws):
    note = create(client).json()
    r = client.get(f"/patients/pat-1/notes/{note['note_id']}", headers=headers("pat-1", "patient"))
    assert r.status_code == 200
    r = client.get("/patients/pat-1/notes", headers=headers("pat-2", "patient"))
    assert r.status_code == 403


# ----- authentication -----

def test_missing_or_wrong_token_is_401(client, aws):
    assert client.get("/patients/pat-1/notes").status_code == 401
    assert client.get("/patients/pat-1/notes", headers=headers(token="wrong")).status_code == 401
    assert audit_items() == []  # unauthenticated callers have no identity to audit


def test_previous_token_still_works_during_rotation(client, aws, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_TOKEN", "new-token")
    monkeypatch.setattr(config, "INTERNAL_TOKEN_PREVIOUS", TOKEN)
    assert client.get("/patients/pat-1/notes", headers=headers(token=TOKEN)).status_code == 200
    assert client.get("/patients/pat-1/notes", headers=headers(token="new-token")).status_code == 200
    assert client.get("/patients/pat-1/notes", headers=headers(token="other")).status_code == 401


def test_fails_closed_when_no_token_configured(client, aws, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_TOKEN", None)
    r = client.get("/patients/pat-1/notes", headers=headers())
    assert r.status_code == 503


def test_unknown_role_or_bad_caller_id_is_401(client, aws):
    assert client.get("/patients/pat-1/notes", headers=headers(role="admin")).status_code == 401
    assert client.get("/patients/pat-1/notes", headers=headers(caller_id="a b")).status_code == 401
    assert client.get("/patients/pat-1/notes", headers=headers(caller_id="")).status_code == 401


# ----- authorization -----

def test_unassigned_clinician_is_denied_and_audited(client, aws):
    r = client.get("/patients/pat-1/notes", headers=headers("doc-2", "doctor"))
    assert r.status_code == 403 and r.json() == {"error": "forbidden"}
    ev = audit_items()
    assert len(ev) == 1 and ev[0]["outcome"] == "denied" and ev[0]["caller_id"] == "doc-2"


def test_patient_cannot_write(client, aws):
    r = create(client, h=headers("pat-1", "patient"))
    assert r.status_code == 403
    assert client.get("/patients/pat-1/notes", headers=headers()).json()["notes"] == []


def test_denial_does_not_reveal_whether_note_exists(client, aws):
    note = create(client).json()
    other = headers("doc-2", "doctor")
    a = client.get(f"/patients/pat-1/notes/{note['note_id']}", headers=other)
    b = client.get("/patients/pat-1/notes/20200101T000000000000Z-aaaaaaaaaaaa", headers=other)
    assert a.status_code == b.status_code == 403 and a.json() == b.json()


def test_note_of_another_patient_cannot_be_read_through_my_patient_id(client, aws):
    db.put_assignment("doc-1", "pat-2")
    note = client.post("/patients/pat-2/notes", json=NOTE, headers=headers()).json()
    r = client.get(f"/patients/pat-1/notes/{note['note_id']}", headers=headers())
    assert r.status_code == 404


def test_missing_note_is_404_for_authorized_caller(client, aws):
    r = client.get("/patients/pat-1/notes/20200101T000000000000Z-aaaaaaaaaaaa", headers=headers())
    assert r.status_code == 404


# ----- audit -----

def test_every_allowed_action_is_audited_without_note_text(client, aws):
    note = create(client).json()
    client.get(f"/patients/pat-1/notes/{note['note_id']}", headers=headers())
    client.get("/patients/pat-1/notes", headers=headers())
    ev = sorted(audit_items(), key=lambda e: e["event_id"])
    assert [e["action"] for e in ev] == ["create_note", "read_note", "list_notes"]
    assert all(e["outcome"] == "allowed" and e["role"] == "doctor" for e in ev)
    assert ev[0]["note_id"] == note["note_id"]
    assert NOTE["text"] not in json.dumps(ev, default=str)


def test_audit_failure_blocks_the_action(client, aws, monkeypatch):
    def boom(item):
        raise RuntimeError("audit store down")

    original = db.put_audit
    monkeypatch.setattr(db, "put_audit", boom)
    r = create(client)
    assert r.status_code == 503 and r.json() == {"error": "audit_unavailable"}
    monkeypatch.setattr(db, "put_audit", original)
    # The note must not exist: the action did not happen.
    assert client.get("/patients/pat-1/notes", headers=headers()).json()["notes"] == []


def test_request_id_is_returned_and_validated(client, aws):
    r = client.get("/patients/pat-1/notes", headers=headers(**{"X-Request-Id": "req-12345678"}))
    assert r.headers["X-Request-Id"] == "req-12345678"
    r = client.get("/patients/pat-1/notes", headers=headers(**{"X-Request-Id": "bad id!"}))
    assert r.headers["X-Request-Id"] != "bad id!" and len(r.headers["X-Request-Id"]) == 32


# ----- validation -----

def test_validation_rejects_bad_bodies_without_echoing_them(client, aws):
    secret = "SYNTHETIC-SECRET-TEXT"
    bad = [
        {"visit_date": "2026-11-09"},                                   # no text
        {"visit_date": "not-a-date", "text": secret},                   # bad date
        {"visit_date": "2026-11-09", "text": ""},                       # empty
        {"visit_date": "2026-11-09", "text": "x" * (config.MAX_NOTE_CHARS + 1)},
        {"visit_date": "2026-11-09", "text": secret + "\x00"},          # NUL
        {"visit_date": "2026-11-09", "text": secret + "‮"},        # bidi override
        {"visit_date": "2026-11-09", "text": secret, "author_id": "x"},  # extra field
    ]
    for body in bad:
        r = create(client, body=body)
        assert r.status_code == 422, body
        assert r.json()["error"] == "invalid_request"
        assert secret not in r.text
    # Rejected input never reaches authorization, the audit table or the notes table.
    assert audit_items() == []
    assert client.get("/patients/pat-1/notes", headers=headers()).json()["notes"] == []


def test_newline_and_tab_are_allowed(client, aws):
    r = create(client, body={"visit_date": "2026-11-09", "text": "line1\nline2\tend"})
    assert r.status_code == 201


def test_bad_ids_are_rejected(client, aws):
    for pid in ["a.b", "a%20b", "x" * 65]:
        assert client.get(f"/patients/{pid}/notes", headers=headers()).status_code in (404, 422)
    assert client.get("/patients/pat-1/notes/not-a-note-id", headers=headers()).status_code == 422
