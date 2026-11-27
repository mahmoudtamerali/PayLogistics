"""Integrity-protected, query-scoped DynamoDB pagination cursors."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Any

import boto3

from common.exceptions import ValidationError

_cached_key: bytes | None = None

def _key() -> bytes:
    """Load the signing key from Secrets Manager and cache it per Lambda."""
    global _cached_key
    if _cached_key is not None:
        return _cached_key
    secret_arn = os.environ.get("CURSOR_HMAC_SECRET_ARN", "")
    if not secret_arn:
        raise RuntimeError("CURSOR_HMAC_SECRET_ARN must be configured for pagination")
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "eu-west-1"
    response = boto3.client("secretsmanager", region_name=region).get_secret_value(
        SecretId=secret_arn
    )
    value = response.get("SecretString", "")
    if len(value) < 32:
        raise RuntimeError("Cursor signing secret must contain at least 32 characters")
    _cached_key = value.encode("utf-8")
    return _cached_key


def encode_cursor(last_key: dict[str, Any] | None, scope: str) -> str | None:
    """Sign the LastEvaluatedKey and bind it to the query's authorization scope."""
    if not last_key:
        return None
    payload = json.dumps(
        {"v": 1, "scope": scope, "key": last_key},
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    mac = hmac.new(_key(), payload, hashlib.sha256).digest()
    token = base64.urlsafe_b64encode(payload + mac).decode("ascii").rstrip("=")
    return token


def decode_cursor(token: str | None, scope: str) -> dict[str, Any] | None:
    """Verify signature and query scope before returning a DynamoDB key."""
    if not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        payload, supplied_mac = raw[:-32], raw[-32:]
        expected_mac = hmac.new(_key(), payload, hashlib.sha256).digest()
        if len(supplied_mac) != 32 or not hmac.compare_digest(supplied_mac, expected_mac):
            raise ValidationError("Invalid pagination token")
        decoded = json.loads(payload.decode("utf-8"))
        if (
            not isinstance(decoded, dict)
            or decoded.get("v") != 1
            or decoded.get("scope") != scope
            or not isinstance(decoded.get("key"), dict)
        ):
            raise ValidationError("Invalid pagination token")
        return decoded["key"]
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("Invalid pagination token") from exc
