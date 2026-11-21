"""DynamoDB access for notes, audit entries and clinician-patient assignments."""
import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from . import config, tracing

_cached = None


def reset_clients():
    """Forget cached AWS clients (used by tests that start a fake AWS)."""
    global _cached
    _cached = None


def _resource():
    global _cached
    if _cached is None:
        kwargs = {"region_name": config.AWS_REGION}
        if config.DYNAMODB_ENDPOINT:
            # DynamoDB Local accepts any credentials; these dummy values are for
            # local use only and never reach AWS.
            kwargs.update(
                endpoint_url=config.DYNAMODB_ENDPOINT,
                aws_access_key_id="local",
                aws_secret_access_key="local",
            )
        _cached = boto3.resource("dynamodb", **kwargs)
        tracing.instrument(_cached.meta.client)  # no-op unless tracing is on
    return _cached


def _table(name: str):
    return _resource().Table(name)


# ---------- local development helpers ----------

_TABLE_SPECS = {
    config.TABLE_NAME: ("patient_id", "note_id"),
    config.AUDIT_TABLE: ("patient_id", "event_id"),
    config.ASSIGNMENTS_TABLE: ("clinician_id", "patient_id"),
}


def ensure_tables():
    """Create the three tables when running locally. Idempotent."""
    db = _resource()
    for name, (hash_key, range_key) in _TABLE_SPECS.items():
        try:
            db.meta.client.describe_table(TableName=name)
            continue
        except ClientError as err:
            if err.response["Error"]["Code"] != "ResourceNotFoundException":
                raise
        db.create_table(
            TableName=name,
            KeySchema=[
                {"AttributeName": hash_key, "KeyType": "HASH"},
                {"AttributeName": range_key, "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": hash_key, "AttributeType": "S"},
                {"AttributeName": range_key, "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        db.meta.client.get_waiter("table_exists").wait(TableName=name)
    return True


# Kept for the Day 1 tests and scripts.
ensure_table = ensure_tables


def table_is_reachable() -> bool:
    """True if all three tables answer a describe call. Used by /health/ready."""
    try:
        client = _resource().meta.client
        for name in _TABLE_SPECS:
            client.describe_table(TableName=name)
        return True
    except Exception:
        return False


# ---------- assignments ----------

def put_assignment(clinician_id: str, patient_id: str):
    _table(config.ASSIGNMENTS_TABLE).put_item(
        Item={"clinician_id": clinician_id, "patient_id": patient_id}
    )


def is_assigned(clinician_id: str, patient_id: str) -> bool:
    resp = _table(config.ASSIGNMENTS_TABLE).get_item(
        Key={"clinician_id": clinician_id, "patient_id": patient_id}
    )
    return "Item" in resp


# ---------- notes ----------

def put_note(item: dict):
    _table(config.TABLE_NAME).put_item(
        Item=item,
        ConditionExpression="attribute_not_exists(note_id)",
    )


def get_note(patient_id: str, note_id: str):
    resp = _table(config.TABLE_NAME).get_item(Key={"patient_id": patient_id, "note_id": note_id})
    return resp.get("Item")


def list_notes(patient_id: str, limit: int):
    resp = _table(config.TABLE_NAME).query(
        KeyConditionExpression=Key("patient_id").eq(patient_id),
        ScanIndexForward=False,  # newest first (note ids sort by time)
        Limit=limit,
    )
    return resp.get("Items", [])


# ---------- audit ----------

def put_audit(item: dict):
    _table(config.AUDIT_TABLE).put_item(Item=item)
