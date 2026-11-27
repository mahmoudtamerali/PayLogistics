"""
Authentication module for SecureCare backend.

Extracts and validates Cognito claims from API Gateway Lambda Proxy
Integration events. The Cognito User Pool Authorizer has already verified
the JWT by the time the Lambda is invoked; this module extracts the
trusted claims from the event context.

IMPORTANT: Never trust role or identity fields from the request body.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from common.exceptions import AuthenticationError

logger = logging.getLogger("securecare.auth")

# The four valid application roles.
VALID_ROLES = frozenset({"patient", "doctor", "staff", "admin"})

# Role precedence – lower index = higher privilege.
# Policy (v4 §7.1): each account must belong to exactly one application
# group; callers with multiple valid roles are rejected rather than
# silently downgraded.
ROLE_PRECEDENCE = ["admin", "doctor", "staff", "patient"]


@dataclass(frozen=True)
class AuthContext:
    """Immutable authentication context derived from Cognito claims."""

    sub: str  # Cognito subject – trusted user identity
    email: str | None = None
    roles: frozenset[str] = field(default_factory=frozenset)
    primary_role: str = "patient"
    cognito_groups: tuple[str, ...] = ()
    source_ip: str = "unknown"
    request_id: str = "unknown"
    name: str | None = None  # display name claim (optional)
    phone: str | None = None  # phone_number claim (optional)

    def has_role(self, role: str) -> bool:
        """Check if the user has a specific role."""
        return role in self.roles

    def is_patient(self) -> bool:
        return self.primary_role == "patient"

    def is_doctor(self) -> bool:
        return self.primary_role == "doctor"

    def is_staff(self) -> bool:
        return self.primary_role == "staff"

    def is_admin(self) -> bool:
        return self.primary_role == "admin"


def extract_auth_context(event: dict[str, Any]) -> AuthContext:
    """
    Extract authenticated identity from API Gateway event.

    The API Gateway Cognito User Pool Authorizer populates the
    ``requestContext.authorizer.claims`` object with verified JWT claims.

    Args:
        event: API Gateway Lambda Proxy Integration event.

    Returns:
        AuthContext with the validated user identity.

    Raises:
        AuthenticationError: If required claims are missing or invalid.
    """
    request_context = event.get("requestContext", {})
    request_id = request_context.get("requestId", "unknown")

    # Source IP from API Gateway
    identity = request_context.get("identity", {})
    source_ip = identity.get("sourceIp", "unknown")

    # Claims populated by Cognito authorizer
    claims = request_context.get("authorizer", {}).get("claims", {})
    if not claims:
        logger.warning("No Cognito claims in request context", extra={
            "request_id": request_id,
        })
        raise AuthenticationError("Authentication required")

    # Extract the Cognito subject (sub) – the trusted user identifier
    sub = claims.get("sub")
    if not sub or not isinstance(sub, str) or len(sub.strip()) == 0:
        logger.warning("Missing or empty Cognito sub claim", extra={
            "request_id": request_id,
        })
        raise AuthenticationError("Invalid authentication token")

    sub = sub.strip()

    # Extract email (optional, for logging/display only)
    email = claims.get("email")

    # Extract display name and phone (optional)
    name_claim = claims.get("name")
    name = name_claim.strip() if isinstance(name_claim, str) and name_claim.strip() else None
    phone_claim = claims.get("phone_number")
    phone = phone_claim.strip() if isinstance(phone_claim, str) and phone_claim.strip() else None

    # Extract and validate Cognito groups
    groups_claim = claims.get("cognito:groups", "")
    if isinstance(groups_claim, list):
        raw_groups = [str(g).strip() for g in groups_claim]
    elif isinstance(groups_claim, str) and groups_claim.strip():
        cleaned = groups_claim.strip().strip("[]")
        raw_groups = [
            g.strip().strip("'\"")
            for g in cleaned.split(",")
            if g.strip().strip("'\"")
        ]
    else:
        raw_groups = []

    # A principal must have exactly one known application group. Silently
    # dropping an unknown group could hide unintended elevated membership.
    normalized_groups = frozenset(g.lower() for g in raw_groups)
    unknown_groups = normalized_groups - VALID_ROLES
    if unknown_groups:
        logger.warning(
            "User has unknown Cognito groups",
            extra={"request_id": request_id, "groups": sorted(unknown_groups)},
        )
        raise AuthenticationError("User has an unsupported application role")
    valid_groups = normalized_groups

    if not valid_groups:
        logger.warning("User has no valid application roles", extra={
            "request_id": request_id,
            "raw_groups": raw_groups,
        })
        raise AuthenticationError("User has no assigned application role")

    # v4 §7.1: reject missing, multiple, or unknown roles. Every account
    # must belong to exactly one application group.
    if len(valid_groups) > 1:
        logger.warning(
            "User belongs to multiple application roles",
            extra={
                "request_id": request_id,
                "roles": sorted(valid_groups),
            },
        )
        raise AuthenticationError("User must belong to exactly one application role")

    primary_role = _resolve_primary_role(valid_groups)

    return AuthContext(
        sub=sub,
        email=email,
        roles=valid_groups,
        primary_role=primary_role,
        cognito_groups=tuple(sorted(valid_groups)),
        source_ip=source_ip,
        request_id=request_id,
        name=name,
        phone=phone,
    )


def _resolve_primary_role(roles: frozenset[str]) -> str:
    """
    Resolve the single primary role from a set of roles.

    Callers are validated to hold exactly one role upstream; this helper
    still applies deterministic precedence as a defensive fallback.
    Role precedence (most to least privileged, lower index = higher):
        admin > doctor > staff > patient
    """
    for role in ROLE_PRECEDENCE:
        if role in roles:
            return role
    # Fallback – should not reach here if VALID_ROLES check passed
    return "patient"
