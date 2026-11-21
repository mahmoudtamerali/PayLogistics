import boto3

from app import config, db


def test_health_is_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["service"] == "records-service"


def test_docs_are_not_exposed(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_ready_when_tables_exist(client, aws):
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready"}


def test_not_ready_when_tables_missing(client):
    from moto import mock_aws

    with mock_aws():
        db.reset_clients()
        assert client.get("/health/ready").status_code == 503


def test_ensure_tables_is_idempotent():
    from moto import mock_aws

    with mock_aws():
        db.reset_clients()
        assert db.ensure_tables() is True
        assert db.ensure_tables() is True
        names = boto3.client("dynamodb", region_name="eu-west-1").list_tables()["TableNames"]
        assert sorted(names) == sorted([config.TABLE_NAME, config.AUDIT_TABLE, config.ASSIGNMENTS_TABLE])
