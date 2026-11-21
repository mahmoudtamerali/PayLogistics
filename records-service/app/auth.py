"""Caller authentication and authorization (DRAFT contract, confirm in T40).

The API's Lambda calls this service from inside the VPC and sends:
  X-Internal-Token : shared secret (Secrets Manager, rotated in T105)
  X-Caller-Id      : id of the signed-in user, already verified by the API
  X-Caller-Role    : doctor | nurse | patient   (admin has no access to notes)
  X-Request-Id     : optional correlation id

This service trusts those headers ONLY because the token proves the caller is
the API. It still enforces the rules itself (defence in depth): a clinician
needs an assignment to the patient, and a patient sees only their own notes.
If no token is configured the service refuses all note requests (fail closed).
"""
import hmac
import uuid
from dataclasses import dataclass

from fastapi import Request

from . import config, db, models, obs


class ApiError(Exception):
    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code


@dataclass(frozen=True)
class Caller:
    caller_id: str
    role: str
    request_id: str


def _token_ok(presented: str) -> bool:
    ok = False
    for secret in (config.INTERNAL_TOKEN, config.INTERNAL_TOKEN_PREVIOUS):
        if secret and hmac.compare_digest(presented.encode(), secret.encode()):
            ok = True  # keep looping: constant work whichever secret matched
    return ok


def authenticate(request: Request) -> Caller:
    """FastAPI dependency: who is calling? Raises ApiError(401/503)."""
    if not config.INTERNAL_TOKEN:
        obs.log("auth_not_configured")
        raise ApiError(503, "service_not_configured")

    h = request.headers
    rid = h.get("x-request-id")
    request_id = rid if models.valid_request_id(rid) else uuid.uuid4().hex
    request.state.request_id = request_id

    presented = h.get("x-internal-token", "")
    caller_id = h.get("x-caller-id", "")
    role = h.get("x-caller-role", "")

    if not presented or not _token_ok(presented):
        obs.metric("DeniedRequests", reason="bad_token", request_id=request_id)
        raise ApiError(401, "unauthorized")
    if not models.valid_id(caller_id) or role not in (
        config.WRITE_ROLES | config.READ_OWN_ROLES
    ):
        obs.metric("DeniedRequests", reason="bad_caller", request_id=request_id)
        raise ApiError(401, "unauthorized")
    return Caller(caller_id=caller_id, role=role, request_id=request_id)


def can_write(caller: Caller, patient_id: str) -> bool:
    return caller.role in config.WRITE_ROLES and db.is_assigned(caller.caller_id, patient_id)


def can_read(caller: Caller, patient_id: str) -> bool:
    if caller.role in config.WRITE_ROLES:
        return db.is_assigned(caller.caller_id, patient_id)
    if caller.role in config.READ_OWN_ROLES:
        return caller.caller_id == patient_id
    return False
