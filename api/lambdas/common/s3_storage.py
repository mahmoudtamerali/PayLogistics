"""
S3 storage operations for SecureCare backend.

Handles pre-signed URL generation for secure file uploads and downloads.
All objects use private storage; no public access is permitted.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from common.config import get_config
from common.exceptions import InternalError

logger = logging.getLogger("securecare.s3_storage")

# Module-level client cache
_s3_client = None


def get_s3_client():
    """Return a cached S3 client configured for signature v4."""
    global _s3_client
    if _s3_client is None:
        cfg = get_config()
        _s3_client = boto3.client(
            "s3",
            region_name=cfg.AWS_REGION,
            config=BotoConfig(signature_version="s3v4"),
        )
    return _s3_client


def reset_client():
    """Reset cached client (for testing)."""
    global _s3_client
    _s3_client = None


def generate_object_key(
    patient_id: str,
    file_id: str,
    file_name: str,
    content_type: str,
) -> str:
    """
    Generate a unique, safe S3 object key.

    Format: patients/{patient_id}/{fileId}/{sanitized_filename}

    The fileId segment keeps every object under its metadata record so the
    quarantine workflow (uploads bucket) and the clean bucket share the
    same key. Never uses user-supplied object keys directly.
    """
    # Sanitize filename – keep only alphanumeric, dots, hyphens, underscores
    safe_name = "".join(
        c if c.isalnum() or c in ".-_" else "_" for c in file_name
    )
    if not safe_name:
        # Map content type to extension
        ext_map = {
            "application/pdf": "file.pdf",
            "image/png": "file.png",
            "image/jpeg": "file.jpg",
        }
        safe_name = ext_map.get(content_type, "file")

    safe_file_id = "".join(
        c if c.isalnum() or c in "-" else "_" for c in str(file_id)
    ) or str(uuid.uuid4())

    return f"patients/{patient_id}/{safe_file_id}/{safe_name}"


def generate_presigned_upload_url(
    bucket: str,
    object_key: str,
    content_type: str,
    max_size_bytes: int,
    expiry_seconds: int = 300,
) -> dict[str, Any]:
    """
    Generate a secure pre-signed URL for file upload (PUT).

    The signature covers SSE-KMS parameters so browser uploads are
    encrypted with the configured customer-managed key even when the
    bucket policy requires an explicit encryption header.

    Args:
        bucket: Target S3 bucket name.
        object_key: S3 object key.
        content_type: Expected content type.
        max_size_bytes: Maximum file size in bytes.
        expiry_seconds: URL expiry in seconds.

    Returns:
        Dict with 'upload_url' and 'fields' (for POST) or just 'upload_url' (for PUT).
    """
    cfg = get_config()
    s3 = get_s3_client()
    try:
        url = s3.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": bucket,
                "Key": object_key,
                "ContentType": content_type,
                "ServerSideEncryption": "aws:kms",
                "SSEKMSKeyId": cfg.KMS_KEY_ARN,
            },
            ExpiresIn=expiry_seconds,
            HttpMethod="PUT",
        )
        return {
            "upload_url": url,
            "object_key": object_key,
            "method": "PUT",
            "headers": {
                "Content-Type": content_type,
                # Required: the presigned signature covers these SSE-KMS
                # headers, so the client must send them verbatim on PUT.
                "x-amz-server-side-encryption": "aws:kms",
                "x-amz-server-side-encryption-aws-kms-key-id": cfg.KMS_KEY_ARN,
            },
            "expires_in_seconds": expiry_seconds,
        }
    except ClientError as e:
        logger.error("Failed to generate presigned upload URL: %s", str(e))
        raise InternalError("Failed to generate upload URL")


def generate_presigned_download_url(
    bucket: str,
    object_key: str,
    expiry_seconds: int = 300,
    response_content_type: str | None = None,
    response_filename: str | None = None,
) -> str:
    """
    Generate a short-lived pre-signed download URL.

    Args:
        bucket: Source S3 bucket name.
        object_key: S3 object key.
        expiry_seconds: URL expiry in seconds.
        response_content_type: Override Content-Type in response.
        response_filename: Suggested download filename.

    Returns:
        Pre-signed download URL string.
    """
    s3 = get_s3_client()
    params: dict[str, Any] = {
        "Bucket": bucket,
        "Key": object_key,
    }
    if response_content_type:
        params["ResponseContentType"] = response_content_type
    if response_filename:
        params["ResponseContentDisposition"] = f'attachment; filename="{response_filename}"'

    try:
        url = s3.generate_presigned_url(
            ClientMethod="get_object",
            Params=params,
            ExpiresIn=expiry_seconds,
        )
        return url
    except ClientError as e:
        logger.error("Failed to generate presigned download URL: %s", str(e))
        raise InternalError("Failed to generate download URL")


def check_object_exists(bucket: str, object_key: str) -> bool:
    """Check if an S3 object exists."""
    s3 = get_s3_client()
    try:
        s3.head_object(Bucket=bucket, Key=object_key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        logger.error("S3 head_object failed: %s", str(e))
        raise InternalError("Failed to verify file existence")


def get_object_metadata(bucket: str, object_key: str) -> dict[str, Any] | None:
    """Get S3 object metadata (content type, size)."""
    s3 = get_s3_client()
    try:
        response = s3.head_object(Bucket=bucket, Key=object_key)
        return {
            "content_type": response.get("ContentType", ""),
            "content_length": response.get("ContentLength", 0),
            "last_modified": response.get("LastModified"),
        }
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return None
        logger.error("S3 head_object failed: %s", str(e))
        raise InternalError("Failed to retrieve file metadata")


def delete_object(bucket: str, object_key: str) -> None:
    """Delete an S3 object."""
    s3 = get_s3_client()
    try:
        s3.delete_object(Bucket=bucket, Key=object_key)
    except ClientError as e:
        logger.error("S3 delete_object failed: %s", str(e))
        raise InternalError("Failed to delete file")
