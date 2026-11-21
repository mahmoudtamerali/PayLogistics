"""Settings read from environment variables. No secrets live in code.

In AWS (Phase 3) the values come from the ECS task definition. The internal
token comes from Secrets Manager through the task definition's `secrets` list
(never from the plain-text `environment` list), see T55 and T105.
"""
import os


def _get(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _csv(name: str, default: str) -> frozenset:
    return frozenset(x.strip() for x in _get(name, default).split(",") if x.strip())


AWS_REGION = _get("AWS_REGION", "eu-west-1")

# Tables. Terraform creates them in AWS; CREATE_TABLE_ON_START is for local use.
TABLE_NAME = _get("RECORDS_TABLE", "securecare-visit-notes")
AUDIT_TABLE = _get("AUDIT_TABLE", "securecare-audit")
ASSIGNMENTS_TABLE = _get("ASSIGNMENTS_TABLE", "securecare-assignments")

# Set only for local development (DynamoDB Local). Leave unset in AWS so the
# SDK uses the DynamoDB gateway endpoint.
DYNAMODB_ENDPOINT = os.environ.get("DYNAMODB_ENDPOINT") or None

# Local only: create the tables on start if missing, and seed one synthetic
# assignment so the API can be tried by hand. Never true in AWS.
CREATE_TABLE_ON_START = _get("CREATE_TABLE_ON_START", "false").lower() == "true"
SEED_LOCAL_DATA = _get("SEED_LOCAL_DATA", "false").lower() == "true"

# Shared secret between the API's Lambda and this service (DRAFT contract, to
# be confirmed with the API owner in T40). Two values are accepted so the
# secret can be rotated without downtime (T105): set the new one in
# INTERNAL_TOKEN and keep the old one in INTERNAL_TOKEN_PREVIOUS until every
# Lambda has the new one.
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN") or None
INTERNAL_TOKEN_PREVIOUS = os.environ.get("INTERNAL_TOKEN_PREVIOUS") or None

# Roles (DRAFT: align with the four user roles in the API's design).
WRITE_ROLES = _csv("WRITE_ROLES", "doctor,nurse")    # need an assignment to the patient
READ_OWN_ROLES = _csv("READ_OWN_ROLES", "patient")   # may read only their own notes

MAX_NOTE_CHARS = int(_get("MAX_NOTE_CHARS", "5000"))

SERVICE_NAME = "records-service"
SERVICE_VERSION = _get("SERVICE_VERSION", "0.2.0")
