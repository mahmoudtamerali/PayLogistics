"""FileRecord model for SecureCare backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

VALID_STATUSES: set[str] = {"PENDING_SCAN", "CLEAN", "QUARANTINED"}


@dataclass
class FileRecord:
    """Represents a file attached to a patient record in securecare-files.

    Attributes:
        fileId: Unique file identifier (Partition Key).
        patient_id: Owning patient's identifier (ByPatient GSI PK).
        objectKey: Private S3 object key — never exposed in API responses.
        fileName: Original sanitized upload file name.
        contentType: MIME content type (application/pdf, image/png, image/jpeg).
        sizeBytes: File size in bytes.
        status: Scan status — PENDING_SCAN, CLEAN, or QUARANTINED.
        uploadedBy: Cognito sub of the user who uploaded the file.
        createdAt: ISO-8601 UTC timestamp of record creation.
    """

    VALID_STATUSES: ClassVar[set[str]] = VALID_STATUSES

    fileId: str
    patient_id: str
    objectKey: str
    fileName: str
    contentType: str
    sizeBytes: int
    status: str
    uploadedBy: str
    createdAt: str

    def __post_init__(self) -> None:
        if self.status not in self.VALID_STATUSES:
            raise ValueError(
                f"Invalid status {self.status!r}. "
                f"Must be one of {sorted(self.VALID_STATUSES)}."
            )
        if not isinstance(self.sizeBytes, int) or self.sizeBytes < 0:
            raise ValueError("sizeBytes must be a non-negative integer.")

    def to_dynamodb_item(self) -> dict[str, Any]:
        """Convert to a DynamoDB resource item dict matching exact schema."""
        return {
            "fileId": self.fileId,
            "patient_id": self.patient_id,
            "objectKey": self.objectKey,
            "fileName": self.fileName,
            "contentType": self.contentType,
            "sizeBytes": self.sizeBytes,
            "status": self.status,
            "uploadedBy": self.uploadedBy,
            "createdAt": self.createdAt,
        }

    @classmethod
    def from_dynamodb_item(cls, item: dict[str, Any]) -> FileRecord:
        """Create a FileRecord instance from a DynamoDB resource item dict."""
        return cls(
            fileId=str(item["fileId"]),
            patient_id=str(item["patient_id"]),
            objectKey=str(item["objectKey"]),
            fileName=str(item["fileName"]),
            contentType=str(item["contentType"]),
            sizeBytes=int(item["sizeBytes"]),
            status=str(item["status"]),
            uploadedBy=str(item["uploadedBy"]),
            createdAt=str(item["createdAt"]),
        )

    def to_api_response(self) -> dict[str, Any]:
        """Return a dict safe for API responses (objectKey intentionally omitted)."""
        return {
            "fileId": self.fileId,
            "patient_id": self.patient_id,
            "fileName": self.fileName,
            "contentType": self.contentType,
            "sizeBytes": self.sizeBytes,
            "status": self.status,
            "uploadedBy": self.uploadedBy,
            "createdAt": self.createdAt,
        }
