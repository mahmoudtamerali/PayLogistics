"""X-Ray tracing (T62): off by default, never leaks identifiers, never breaks a request."""
import importlib
import json
import sys

import pytest
from fastapi.testclient import TestClient

from app import config, db, tracing

from .conftest import TOKEN, headers

TRACE_ROOT = "1-5759e988-bd862e3fe1be46a994272793"
TRACE_PARENT = "53995c3f42cd8ad8"


@pytest.fixture
def traced(monkeypatch):
    """The app reloaded with XRAY_ENABLED=true and a fake daemon that records what would be sent."""
    from aws_xray_sdk.core.emitters.udp_emitter import UDPEmitter

    sent = []
    monkeypatch.setattr(UDPEmitter, "send_entity", lambda self, entity: sent.append(entity.to_dict()))
    monkeypatch.setenv(tracing.ENABLED_ENV, "true")
    import app.main as main

    main = importlib.reload(main)
    assert tracing.enabled()
    with TestClient(main.app, raise_server_exceptions=False) as c:
        yield c, sent
    monkeypatch.delenv(tracing.ENABLED_ENV)
    tracing.reset()
    importlib.reload(main)


def _note_body():
    return {"visit_date": "2026-11-20", "text": "synthetic note"}


def test_off_by_default(client):
    assert tracing.enabled() is False
    assert client.get("/health").status_code == 200


def test_segment_has_route_template_and_no_identifiers(traced, aws):
    client, sent = traced
    r = client.get("/patients/pat-1/notes", headers=headers())
    assert r.status_code == 200
    assert len(sent) == 1
    seg = sent[0]
    assert seg["name"] == "records-service"
    assert seg["http"]["request"]["url"] == "/patients/{patient_id}/notes"
    assert seg["http"]["response"]["status"] == 200
    assert seg["annotations"]["role"] == "doctor"
    text = json.dumps(seg)
    for secret in ("pat-1", "doc-1", TOKEN):
        assert secret not in text


def test_dynamodb_subsegments_carry_operation_and_table_only(traced, aws):
    client, sent = traced
    client.post("/patients/pat-1/notes", json=_note_body(), headers=headers())
    seg = sent[0]
    subs = seg["subsegments"]
    assert subs, "expected DynamoDB subsegments"
    assert all(s["name"] == "DynamoDB" and s["namespace"] == "aws" for s in subs)
    operations = {s["aws"]["operation"] for s in subs}
    assert {"PutItem", "GetItem"} <= operations
    assert all(s["aws"]["table_name"] for s in subs)
    assert "synthetic note" not in json.dumps(seg)


def test_client_error_is_flagged_as_error(traced, aws):
    client, sent = traced
    r = client.get("/patients/pat-1/notes", headers=headers(token="wrong"))
    assert r.status_code == 401
    assert sent[0]["error"] is True
    assert sent[0].get("fault") in (None, False)


def test_server_error_is_flagged_as_fault(traced, monkeypatch):
    client, sent = traced
    monkeypatch.setattr(config, "INTERNAL_TOKEN", None)
    r = client.get("/patients/pat-1/notes", headers=headers())
    assert r.status_code == 503
    assert sent[0]["fault"] is True


def test_trace_header_is_continued(traced):
    client, sent = traced
    client.get("/health", headers={"X-Amzn-Trace-Id": f"Root={TRACE_ROOT};Parent={TRACE_PARENT};Sampled=1"})
    assert sent[0]["trace_id"] == TRACE_ROOT
    assert sent[0]["parent_id"] == TRACE_PARENT


def test_unsampled_request_is_not_sent(traced):
    client, sent = traced
    r = client.get("/health", headers={"X-Amzn-Trace-Id": f"Root={TRACE_ROOT};Sampled=0"})
    assert r.status_code == 200
    assert sent == []


def test_unmatched_route_does_not_echo_the_path(traced):
    client, sent = traced
    client.get("/patients/pat-9/secret-path-xyz")
    assert "pat-9" not in json.dumps(sent[0])
    assert "secret-path-xyz" not in json.dumps(sent[0])


def test_daemon_failure_does_not_break_requests(traced, monkeypatch):
    client, sent = traced

    def boom(entity):
        raise OSError("daemon unreachable")

    monkeypatch.setattr(tracing._emitter, "send_entity", boom)
    assert client.get("/health").status_code == 200


def test_missing_sdk_leaves_service_working(monkeypatch):
    monkeypatch.setenv(tracing.ENABLED_ENV, "true")
    for name in (
        "aws_xray_sdk.core.emitters.udp_emitter",
        "aws_xray_sdk.core.models",
        "aws_xray_sdk.core.models.segment",
        "aws_xray_sdk.core.models.subsegment",
        "aws_xray_sdk.core.models.trace_header",
    ):
        monkeypatch.setitem(sys.modules, name, None)  # "import halted": behaves like a missing package
    tracing.reset()
    import app.main as main

    main = importlib.reload(main)
    assert tracing.enabled() is False
    with TestClient(main.app) as c:
        assert c.get("/health").status_code == 200
    monkeypatch.undo()
    monkeypatch.delenv(tracing.ENABLED_ENV, raising=False)
    tracing.reset()
    importlib.reload(main)
