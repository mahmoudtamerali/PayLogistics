"""Appointment model for SecureCare backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

VALID_VISIT_TYPES: set[str] = {"consultation", "follow_up", "lab_visit"}
VALID_STATUSES: set[str] = {"BOOKED", "CANCELLED", "COMPLETED"}


@dataclass
class Appointment:
    """Represents a scheduled appointment in securecare-appointments.

    Attributes:
        appointment_id: Unique appointment identifier (Partition Key).
        patient_id: Owning patient's identifier (ByPatient GSI PK).
        clinician_id: Assigned clinician's identifier (ByDoctor GSI PK).
        startsAt: ISO-8601 UTC datetime when the appointment begins.
        day: UTC date string (YYYY-MM-DD) derived from startsAt (ByDay GSI PK).
        visitType: One of ``consultation``, ``follow_up``, ``lab_visit``.
        status: One of ``BOOKED``, ``CANCELLED``, ``COMPLETED``.
        createdBy: Cognito sub of the user who created the appointment.
        createdAt: ISO-8601 UTC timestamp of record creation.
    """

    VALID_VISIT_TYPES: ClassVar[set[str]] = VALID_VISIT_TYPES
    VALID_STATUSES: ClassVar[set[str]] = VALID_STATUSES

    appointment_id: str
    patient_id: str
    clinician_id: str
    startsAt: str
    day: str
    visitType: str
    status: str
    createdBy: str
    createdAt: str

    def __post_init__(self) -> None:
        if self.visitType not in self.VALID_VISIT_TYPES:
            raise ValueError(
                f"Invalid visitType {self.visitType!r}. "
                f"Must be one of {sorted(self.VALID_VISIT_TYPES)}."
            )
        if self.status not in self.VALID_STATUSES:
            raise ValueError(
                f"Invalid status {self.status!r}. "
                f"Must be one of {sorted(self.VALID_STATUSES)}."
            )

    def to_dynamodb_item(self) -> dict[str, Any]:
        """Convert to a DynamoDB resource item dict matching exact schema."""
        return {
            "appointment_id": self.appointment_id,
            "patient_id": self.patient_id,
            "clinician_id": self.clinician_id,
            "startsAt": self.startsAt,
            "day": self.day,
            "visitType": self.visitType,
            "status": self.status,
            "createdBy": self.createdBy,
            "createdAt": self.createdAt,
        }

    @classmethod
    def from_dynamodb_item(cls, item: dict[str, Any]) -> Appointment:
        """Create an Appointment instance from a DynamoDB resource item dict."""
        return cls(
            appointment_id=str(item["appointment_id"]),
            patient_id=str(item["patient_id"]),
            clinician_id=str(item["clinician_id"]),
            startsAt=str(item["startsAt"]),
            day=str(item["day"]),
            visitType=str(item["visitType"]),
            status=str(item["status"]),
            createdBy=str(item["createdBy"]),
            createdAt=str(item["createdAt"]),
        )

    def to_api_response(self) -> dict[str, Any]:
        """Return a dict safe for API responses."""
        return {
            "appointment_id": self.appointment_id,
            "patient_id": self.patient_id,
            "clinician_id": self.clinician_id,
            "startsAt": self.startsAt,
            "day": self.day,
            "visitType": self.visitType,
            "status": self.status,
            "createdBy": self.createdBy,
            "createdAt": self.createdAt,
        }
