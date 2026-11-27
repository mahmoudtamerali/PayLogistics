"""
Identity resolution layer for SecureCare backend.

Maps Cognito ``sub`` (the trusted user identity) to application-level
identifiers such as ``patient_id`` or ``clinician_id``.

IMPORTANT: Do not automatically assume that Cognito sub equals patient_id.
The application uses an explicit, configurable identity-mapping strategy.

Supported strategies:
  - "cognito_sub": Uses the Cognito sub directly as patient_id/clinician_id.
    This is the default and simplest approach. It requires that patient_id
    and clinician_id values stored in DynamoDB match the Cognito sub of
    the corresponding user.
  - "lookup_table": (Future) Uses a separate identity mapping table.
    Not yet implemented.

If the mapping cannot be established securely, access is denied.
"""

from __future__ import annotations

import logging

from common.auth import AuthContext
from common.config import get_config
from common.exceptions import AuthenticationError, ForbiddenError

logger = logging.getLogger("securecare.identity")


def resolve_patient_id(auth: AuthContext) -> str:
    """
    Resolve the authenticated user's patient_id.

    Only valid for users with the 'patient' role.

    Args:
        auth: Authenticated user context.

    Returns:
        The patient_id for the authenticated user.

    Raises:
        ForbiddenError: If the user is not a patient.
        AuthenticationError: If identity mapping fails.
    """
    if not auth.is_patient():
        raise ForbiddenError("Only patients can access their own patient identity")

    cfg = get_config()
    strategy = cfg.IDENTITY_MAPPING_STRATEGY

    if strategy == "cognito_sub":
        return auth.sub
    elif strategy == "lookup_table":
        # Future: query identity mapping table
        raise AuthenticationError(
            "Identity lookup table strategy is not yet implemented. "
            "Configure IDENTITY_MAPPING_STRATEGY=cognito_sub or deploy "
            "the identity mapping table."
        )
    else:
        logger.error("Unknown identity mapping strategy: %s", strategy)
        raise AuthenticationError("Identity mapping configuration error")


def resolve_clinician_id(auth: AuthContext) -> str:
    """
    Resolve the authenticated user's clinician_id.

    Only valid for users with the 'doctor' role.

    Args:
        auth: Authenticated user context.

    Returns:
        The clinician_id for the authenticated user.

    Raises:
        ForbiddenError: If the user is not a doctor.
        AuthenticationError: If identity mapping fails.
    """
    if not auth.is_doctor():
        raise ForbiddenError("Only doctors can access their clinician identity")

    cfg = get_config()
    strategy = cfg.IDENTITY_MAPPING_STRATEGY

    if strategy == "cognito_sub":
        return auth.sub
    elif strategy == "lookup_table":
        raise AuthenticationError(
            "Identity lookup table strategy is not yet implemented."
        )
    else:
        logger.error("Unknown identity mapping strategy: %s", strategy)
        raise AuthenticationError("Identity mapping configuration error")


def resolve_actor_id(auth: AuthContext) -> str:
    """
    Resolve the authenticated user's application-level actor ID.

    Uses Cognito sub as the actor identity regardless of role.
    This is the trusted identity for audit purposes.

    Args:
        auth: Authenticated user context.

    Returns:
        The actor_id for the authenticated user.
    """
    return auth.sub


def get_user_patient_id(auth: AuthContext) -> str | None:
    """
    Attempt to resolve a patient_id for the user, returning None if
    the user is not a patient.
    """
    if auth.is_patient():
        return resolve_patient_id(auth)
    return None
