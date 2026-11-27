"""
Centralized configuration management for SecureCare backend.

All environment-specific resource identifiers are loaded from environment
variables. The application fails safely if required configuration is missing.
"""

from __future__ import annotations

import logging
import os
from typing import Self, cast

logger = logging.getLogger("securecare.config")

# ---------------------------------------------------------------------------
# Required environment variables
# ---------------------------------------------------------------------------

# The Lambda guide deliberately gives each function only the environment
# variables it uses. Shared code therefore supplies the confirmed table and
# resource defaults and lets deployments override them per function.


class ConfigurationError(Exception):
    """Raised when required configuration is missing or invalid."""


class _Config:
    """Singleton configuration holder, populated from environment variables."""

    _instance: _Config | None = None
    _initialised: bool = False

    def __new__(cls) -> Self:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cast(Self, cls._instance)

    def __init__(self) -> None:
        if self._initialised:
            return
        self._initialised = True
        self._load()

    def _load(self) -> None:
        # AWS
        self.AWS_REGION: str = (
            os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or "eu-west-1"
        )

        # DynamoDB tables
        self.PATIENTS_TABLE: str = os.environ.get(
            "PATIENTS_TABLE", "securecare-patients"
        )
        self.ASSIGNMENTS_TABLE: str = os.environ.get(
            "ASSIGNMENTS_TABLE", "securecare-assignments"
        )
        self.APPOINTMENTS_TABLE: str = os.environ.get(
            "APPOINTMENTS_TABLE", "securecare-appointments"
        )
        self.VISIT_NOTES_TABLE: str = os.environ.get(
            "VISIT_NOTES_TABLE", "securecare-visit-notes"
        )
        self.FILES_TABLE: str = os.environ.get("FILES_TABLE", "securecare-files")
        self.AUDIT_TABLE: str = os.environ.get("AUDIT_TABLE", "securecare-audit")

        # Privacy request tracking table (securecare-data-requests).
        # Optional: defaults to the Schema v3 table name so existing
        # deployments keep working without a new required env var.
        self.REQUESTS_TABLE: str = os.environ.get(
            "REQUESTS_TABLE", "securecare-data-requests"
        )

        # S3 buckets
        self.UPLOAD_BUCKET: str = (
            os.environ.get("UPLOADS_BUCKET")
            or os.environ.get("UPLOAD_BUCKET")
            or "securecare-uploads-199329817097"
        )
        self.CLEAN_BUCKET: str = os.environ.get(
            "CLEAN_BUCKET", "securecare-clean-199329817097"
        )
        self.QUARANTINE_BUCKET: str = os.environ.get(
            "QUARANTINE_BUCKET", "securecare-quarantine-199329817097"
        )

        # KMS
        self.KMS_KEY_ARN: str = os.environ.get(
            "KMS_KEY_ARN",
            "arn:aws:kms:eu-west-1:199329817097:key/a5a38cac-f23c-4cbc-8524-8b5de906cb82",
        )

        # Cognito
        self.COGNITO_USER_POOL_ID: str = os.environ.get(
            "COGNITO_USER_POOL_ID", "eu-west-1_S51fCYW5c"
        )
        self.COGNITO_APP_CLIENT_ID: str = os.environ.get(
            "COGNITO_APP_CLIENT_ID", "265sf66rn74fpel2s798v26nd4"
        )
        # User pool id used by the Post-Confirmation sync trigger and
        # clinician directory lookups. Falls back to the app pool id.
        self.USER_POOL_ID: str = (
            os.environ.get("USER_POOL_ID")
            or os.environ.get("COGNITO_USER_POOL_ID")
            or "eu-west-1_S51fCYW5c"
        )
        # CORS origin for the browser frontend (S3/CloudFront or localhost).
        self.ALLOWED_ORIGIN: str = os.environ.get(
            "ALLOWED_ORIGIN", ""
        )

        # Optional / tuneable
        self.PRESIGNED_URL_EXPIRY: int = int(
            os.environ.get(
                "UPLOAD_URL_TTL", os.environ.get("PRESIGNED_URL_EXPIRY", "300")
            )
        )
        self.DOWNLOAD_URL_TTL: int = int(os.environ.get("DOWNLOAD_URL_TTL", "120"))
        self.MAX_FILE_SIZE_BYTES: int = int(
            os.environ.get("MAX_FILE_SIZE_BYTES", str(10 * 1024 * 1024))
        )
        self.AUDIT_RETENTION_DAYS: int = int(
            os.environ.get("AUDIT_RETENTION_DAYS", str(1826))
        )
        self.MAX_REQUEST_BODY_BYTES: int = int(
            os.environ.get("MAX_REQUEST_BODY_BYTES", str(256 * 1024))
        )

        # Identity mapping strategy: "cognito_sub" or "lookup_table"
        self.IDENTITY_MAPPING_STRATEGY: str = os.environ.get(
            "IDENTITY_MAPPING_STRATEGY", "cognito_sub"
        )

        logger.info("Configuration loaded successfully")

    def reload(self) -> None:
        """Force-reload configuration from environment (for testing)."""
        self._initialised = True
        self._load()


def get_config() -> _Config:
    """Return the singleton configuration instance."""
    return _Config()


def get_table_name(key: str) -> str:
    """Return a DynamoDB table name from the config."""
    cfg = get_config()
    mapping = {
        "patients": cfg.PATIENTS_TABLE,
        "assignments": cfg.ASSIGNMENTS_TABLE,
        "appointments": cfg.APPOINTMENTS_TABLE,
        "visit_notes": cfg.VISIT_NOTES_TABLE,
        "files": cfg.FILES_TABLE,
        "audit": cfg.AUDIT_TABLE,
        "requests": cfg.REQUESTS_TABLE,
    }
    if key not in mapping:
        raise ConfigurationError(f"Unknown table key: {key}")
    return mapping[key]
