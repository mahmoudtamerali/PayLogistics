"""
Cognito helpers for SecureCare backend.

Centralises User Pool client access, doctor directory lookups, display
name resolution, and group membership management used by the
Post-Confirmation sync trigger and the clinician directory endpoint.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

from common.config import get_config

logger = logging.getLogger("securecare.cognito")

# Module-level client cache (reused across Lambda invocations)
_cognito_client = None

# Short-lived caches to absorb Lambda container reuse.
_DIRECTORY_TTL_SECONDS = 60
_NAME_TTL_SECONDS = 300
_MISS_TTL_SECONDS = 60

_directory_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_name_cache: dict[str, tuple[float, str | None]] = {}

PATIENT_GROUP = "patient"
DOCTOR_GROUP = "doctor"


def get_cognito_client():
    """Return a cached Cognito Identity Provider client."""
    global _cognito_client
    if _cognito_client is None:
        cfg = get_config()
        _cognito_client = boto3.client("cognito-idp", region_name=cfg.AWS_REGION)
    return _cognito_client


def reset_cognito_cache() -> None:
    """Reset cached client and lookup caches (for testing)."""
    global _cognito_client
    _cognito_client = None
    _directory_cache.clear()
    _name_cache.clear()


def derive_display_name(email: str | None) -> str:
    """
    Derive a human-friendly default display name from an email prefix.

    Example: "john.doe@example.com" -> "John Doe".
    Falls back to "Patient" when no usable email is available.
    """
    if not email or "@" not in email:
        return "Patient"
    prefix = email.split("@", 1)[0].strip()
    if not prefix:
        return "Patient"
    words = [w for w in prefix.replace(".", " ").replace("_", " ").split(" ") if w]
    if not words:
        return "Patient"
    return " ".join(w.capitalize() for w in words)


def list_group_users(
    group_name: str, user_pool_id: str | None = None
) -> list[dict[str, Any]]:
    """
    List all users in a Cognito group (the doctor directory).

    Returns a list of dicts with ``sub``, ``email``, ``name`` and
    ``status`` keys. Results are cached for a short TTL.
    """
    cache_key = f"{user_pool_id or ''}:{group_name}"
    now = time.monotonic()
    cached = _directory_cache.get(cache_key)
    if cached and now < cached[0]:
        return cached[1]

    cfg = get_config()
    pool_id = user_pool_id or cfg.USER_POOL_ID
    client = get_cognito_client()

    users: list[dict[str, Any]] = []
    pagination_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"UserPoolId": pool_id, "GroupName": group_name}
        if pagination_token:
            kwargs["PaginationToken"] = pagination_token
        response = client.list_users_in_group(**kwargs)
        for entry in response.get("Users", []):
            attributes = {
                attr["Name"]: attr["Value"] for attr in entry.get("Attributes", [])
            }
            users.append(
                {
                    # Appointment identity is always the Cognito UUID claim.
                    # A username is not a substitute for a missing `sub`.
                    "sub": attributes.get("sub"),
                    "email": attributes.get("email", ""),
                    "name": attributes.get("name", ""),
                    "status": entry.get("UserStatus"),
                    "enabled": entry.get("Enabled", True),
                }
            )
        pagination_token = response.get("PaginationToken")
        if not pagination_token:
            break

    _directory_cache[cache_key] = (now + _DIRECTORY_TTL_SECONDS, users)
    return users


def get_user_display_name(username: str) -> str | None:
    """
    Resolve a user's display name via ``AdminGetUser``.

    Prefers the ``name`` attribute, falls back to a name derived from
    the email address. Returns None when the user cannot be resolved.
    """
    now = time.monotonic()
    cached = _name_cache.get(username)
    if cached and now < cached[0]:
        return cached[1]

    cfg = get_config()
    client = get_cognito_client()
    resolved: str | None = None
    try:
        response = client.admin_get_user(
            UserPoolId=cfg.USER_POOL_ID, Username=username
        )
        attributes = {
            attr["Name"]: attr["Value"]
            for attr in response.get("UserAttributes", [])
        }
        name = (attributes.get("name") or "").strip()
        if name:
            resolved = name
        else:
            resolved = derive_display_name(attributes.get("email")) or None
    except ClientError as exc:
        logger.debug("admin_get_user failed: %s", exc)
        resolved = None
    except Exception:  # noqa: BLE001 - name lookup must never break a response
        logger.exception("Unexpected error resolving clinician display name")
        resolved = None

    ttl = _NAME_TTL_SECONDS if resolved else _MISS_TTL_SECONDS
    _name_cache[username] = (now + ttl, resolved)
    return resolved


def add_user_to_group(sub: str, group_name: str, user_pool_id: str | None = None) -> None:
    """Add a Cognito user to a group. Raises ClientError on failure."""
    cfg = get_config()
    pool_id = user_pool_id or cfg.USER_POOL_ID
    client = get_cognito_client()
    client.admin_add_user_to_group(
        UserPoolId=pool_id, GroupName=group_name, Username=sub
    )
