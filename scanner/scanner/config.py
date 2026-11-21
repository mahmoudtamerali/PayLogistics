"""Settings from environment variables (set by the ECS task definition in T55)."""
import os


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


AWS_REGION = _get("AWS_REGION", "eu-west-1")

UPLOADS_BUCKET = _get("UPLOADS_BUCKET")
CLEAN_BUCKET = _get("CLEAN_BUCKET")
QUARANTINE_BUCKET = _get("QUARANTINE_BUCKET")
QUEUE_URL = _get("QUEUE_URL")

# Largest file we scan. clamd's StreamMaxLength is set to the same number, so
# the two limits cannot disagree. Bigger files go to quarantine unscanned.
MAX_BYTES = int(_get("MAX_BYTES", str(25 * 1024 * 1024)))

# clamd: a unix socket (in the container) or host:port (tests).
CLAMD_SOCKET = _get("CLAMD_SOCKET", "/tmp/clamd.sock")
CLAMD_HOST = _get("CLAMD_HOST")
CLAMD_PORT = int(_get("CLAMD_PORT", "3310"))
CLAMD_TIMEOUT = float(_get("CLAMD_TIMEOUT", "120"))

HEARTBEAT_FILE = _get("HEARTBEAT_FILE", "/tmp/scanner-heartbeat")
POLL_WAIT_SECONDS = int(_get("POLL_WAIT_SECONDS", "20"))
QUEUE_ERROR_BACKOFF = float(_get("QUEUE_ERROR_BACKOFF", "5"))

SERVICE_NAME = "scanner"
NAMESPACE = "SecureCare/Scanner"


def missing_settings() -> list:
    needed = {
        "UPLOADS_BUCKET": UPLOADS_BUCKET,
        "CLEAN_BUCKET": CLEAN_BUCKET,
        "QUARANTINE_BUCKET": QUARANTINE_BUCKET,
        "QUEUE_URL": QUEUE_URL,
    }
    return [k for k, v in needed.items() if not v]
