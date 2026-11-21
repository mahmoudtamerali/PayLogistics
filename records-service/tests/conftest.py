import os

# Must be set before the app modules are imported.
os.environ["AWS_REGION"] = "eu-west-1"
os.environ["AWS_DEFAULT_REGION"] = "eu-west-1"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ.pop("DYNAMODB_ENDPOINT", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from moto import mock_aws  # noqa: E402

from app import config, db  # noqa: E402
from app.main import app  # noqa: E402

TOKEN = "unit-test-token"


@pytest.fixture(autouse=True)
def fresh_clients(monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_TOKEN", TOKEN)
    monkeypatch.setattr(config, "INTERNAL_TOKEN_PREVIOUS", None)
    db.reset_clients()
    yield
    db.reset_clients()


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def aws():
    """A fake AWS with all three tables and one synthetic assignment."""
    with mock_aws():
        db.reset_clients()
        db.ensure_tables()
        db.put_assignment("doc-1", "pat-1")
        db.put_assignment("nurse-1", "pat-1")
        yield


def headers(caller_id="doc-1", role="doctor", token=TOKEN, **extra):
    h = {"X-Internal-Token": token, "X-Caller-Id": caller_id, "X-Caller-Role": role}
    h.update(extra)
    return h
