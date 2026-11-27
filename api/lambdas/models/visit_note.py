"""VisitNote model for SecureCare backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_TITLE_LENGTH = 120
MAX_NOTE_LENGTH = 5000


@dataclass
class VisitNote:
    """Represents a clinical visit note in securecare-visit-notes.

    Attributes:
        patient_id: Owning patient's identifier (Partition Key).
        note_id: Unique note identifier (Sort Key, ISO timestamp + UUID).
        clinician_id: Authoring clinician's identifier.
        title: Short title (max 120 characters).
        note: Note body (max 5000 characters).
        created_at: ISO-8601 UTC timestamp of record creation.
        updated_at: ISO-8601 UTC timestamp of last update (optional).
    """

    patient_id: str
    note_id: str
    clinician_id: str
    title: str
    note: str
    created_at: str
    updated_at: str | None = field(default=None)

    def __post_init__(self) -> None:
        if len(self.title) > MAX_TITLE_LENGTH:
            raise ValueError(
                f"title must be at most {MAX_TITLE_LENGTH} characters "
                f"(got {len(self.title)})."
            )
        if len(self.note) > MAX_NOTE_LENGTH:
            raise ValueError(
                f"note must be at most {MAX_NOTE_LENGTH} characters "
                f"(got {len(self.note)})."
            )

    def to_dynamodb_item(self) -> dict[str, Any]:
        """Convert to a DynamoDB resource item dict matching exact schema."""
        item: dict[str, Any] = {
            "patient_id": self.patient_id,
            "note_id": self.note_id,
            "clinician_id": self.clinician_id,
            "title": self.title,
            "note": self.note,
            "created_at": self.created_at,
        }
        if self.updated_at is not None:
            item["updated_at"] = self.updated_at
        return item

    @classmethod
    def from_dynamodb_item(cls, item: dict[str, Any]) -> VisitNote:
        """Create a VisitNote instance from a DynamoDB resource item dict."""
        return cls(
            patient_id=str(item["patient_id"]),
            note_id=str(item["note_id"]),
            clinician_id=str(item["clinician_id"]),
            title=str(item["title"]),
            note=str(item["note"]),
            created_at=str(item["created_at"]),
            updated_at=str(item["updated_at"]) if item.get("updated_at") is not None else None,
        )

    def to_api_response(self) -> dict[str, Any]:
        """Return a dict for API responses."""
        resp: dict[str, Any] = {
            "patient_id": self.patient_id,
            "note_id": self.note_id,
            "clinician_id": self.clinician_id,
            "title": self.title,
            "note": self.note,
            "created_at": self.created_at,
        }
        if self.updated_at is not None:
            resp["updated_at"] = self.updated_at
        return resp
