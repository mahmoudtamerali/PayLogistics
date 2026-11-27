"""
Authorization framework for SecureCare backend.

Implements Role-Based Access Control (RBAC) and Resource-Level Authorization.
All authorization decisions are made server-side; frontend restrictions are
never trusted.

Design principles:
- Fail closed: deny by default.
- Explicit allow: every permitted action is explicitly listed.
- Object-level: resource ownership is verified on every sensitive operation.
"""

from __future__ import annotations

import logging

from common.auth import AuthContext
from common.exceptions import ForbiddenError

logger = logging.getLogger("securecare.authorization")

# ---------------------------------------------------------------------------
# Role-Permission matrix
# ---------------------------------------------------------------------------
# Format: "resource:action" -> set of roles allowed
# ---------------------------------------------------------------------------

PERMISSIONS: dict[str, set[str]] = {
    # Patients
    "patient:read_own": {"patient", "doctor", "staff", "admin"},
    "patient:update_own": {"patient"},
    "patient:read_by_id": {"doctor", "staff", "admin"},

    # Assignments
    "assignment:list_own": {"doctor"},
    "assignment:list_all": {"admin"},
    "assignment:create": {"admin"},
    "assignment:delete": {"admin"},
    "assignment:list_patient_doctors": {"patient", "doctor", "admin"},

    # Appointments
    "appointment:list_own": {"patient", "doctor", "staff", "admin"},
    "appointment:create": {"patient", "staff", "admin"},
    "appointment:read": {"patient", "doctor", "staff", "admin"},
    "appointment:update": {"doctor", "staff", "admin"},
    "appointment:cancel_own": {"patient"},
    "appointment:cancel_any": {"staff", "admin"},
    "appointment:list_by_doctor": {"doctor"},
    "appointment:list_by_day": {"staff", "admin"},

    # Clinician directory (doctor picker)
    "clinician:list": {"patient", "doctor", "staff", "admin"},

    # Visit Notes
    "visit_note:list_patient": {"patient", "doctor"},
    "visit_note:create": {"doctor"},
    "visit_note:read": {"patient", "doctor"},

    # Files
    "file:request_upload": {"patient", "doctor", "staff"},
    "file:complete_upload": {"patient", "doctor", "staff"},
    "file:list_own": {"patient"},
    "file:list_patient": {"doctor", "staff", "admin"},
    "file:read_metadata": {"patient", "doctor", "staff", "admin"},
    "file:download": {"patient", "doctor"},

    # Export
    "export:request_own": {"patient"},
    "export:check_status": {"patient", "admin"},

    # Erasure
    "erasure:request_own": {"patient"},
    "erasure:check_status": {"patient", "admin"},
    "erasure:approve": {"admin"},
    "erasure:execute": {"admin"},

    # Audit
    "audit:read_own": {"patient"},
    "audit:read_patient": {"admin"},
    "audit:read_by_day": {"admin"},
    "audit:list_all": {"admin"},
}


def check_permission(auth: AuthContext, permission: str) -> None:
    """
    Check if the authenticated user has the required permission.

    Args:
        auth: Authenticated user context.
        permission: Permission string (e.g. "patient:read_own").

    Raises:
        ForbiddenError: If the user does not have the required permission.
    """
    allowed_roles = PERMISSIONS.get(permission)
    if allowed_roles is None:
        logger.error("Unknown permission requested: %s", permission)
        raise ForbiddenError()

    # Enforce the single validated application role.
    if auth.primary_role not in allowed_roles:
        logger.warning(
            "Authorization denied: role=%s permission=%s",
            auth.primary_role,
            permission,
        )
        from common.audit import log_denied

        log_denied(
            auth,
            "SYSTEM",
            f"AUTHORIZATION_DENIED_{permission.upper().replace(':', '_')}",
            resource_type="authorization",
            detail={"permission": permission},
        )
        raise ForbiddenError()


def require_role(auth: AuthContext, *roles: str) -> None:
    """
    Require the user's resolved primary role to match one of the specified roles.

    Args:
        auth: Authenticated user context.
        *roles: One or more role names.

    Raises:
        ForbiddenError: If the user does not have any of the required roles.
    """
    if auth.primary_role not in roles:
        logger.warning(
            "Role check failed: role=%s required=%s",
            auth.primary_role,
            roles,
        )
        from common.audit import log_denied

        log_denied(
            auth,
            "SYSTEM",
            "AUTHORIZATION_DENIED_ROLE",
            resource_type="authorization",
            detail={"required_roles": list(roles)},
        )
        raise ForbiddenError()


def require_self_or_role(
    auth: AuthContext,
    resource_owner_id: str,
    *fallback_roles: str,
) -> None:
    """
    Require that the user is the resource owner OR has a fallback primary role.

    Args:
        auth: Authenticated user context.
        resource_owner_id: The ID of the resource owner.
        *fallback_roles: Roles that bypass the ownership check.

    Raises:
        ForbiddenError: If neither condition is met.
    """
    if auth.sub == resource_owner_id:
        return
    if fallback_roles and auth.primary_role in fallback_roles:
        return
    raise ForbiddenError()


def check_resource_access(
    auth: AuthContext,
    resource_type: str,
    resource_owner_id: str | None = None,
    required_permission: str | None = None,
) -> None:
    """
    Combined role + resource-level authorization check.

    Args:
        auth: Authenticated user context.
        resource_type: Type of resource being accessed.
        resource_owner_id: Owner of the resource (for ownership checks).
        required_permission: Permission to check.

    Raises:
        ForbiddenError: If access is denied.
    """
    if required_permission:
        check_permission(auth, required_permission)

    # For patient role, enforce ownership
    if auth.is_patient() and resource_owner_id and auth.sub != resource_owner_id:
        logger.warning(
            "Patient resource access denied: user=%s resource_owner=%s resource_type=%s",
            auth.sub,
            resource_owner_id,
            resource_type,
        )
        raise ForbiddenError()
