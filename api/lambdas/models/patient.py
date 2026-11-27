"""Patient model for SecureCare backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Patient:
    """Represents a patient record in securecare-patients.

    Attributes:
        patient_id: Unique identifier for the patient (Partition Key).
        fullName: Patient's full legal name (1-150 chars).
        dateOfBirth: Date of birth in ISO-8601 format (YYYY-MM-DD).
        email: Contact email address.
        phone: Contact phone number (E.164).
        createdAt: ISO-8601 UTC timestamp of record creation.
    """

    patient_id: str
    fullName: str
    dateOfBirth: str
    email: str
    phone: str
    createdAt: str

    def to_dynamodb_item(self) -> dict[str, Any]:
        """Convert to a DynamoDB resource item dict matching exact schema."""
        return {
            "patient_id": self.patient_id,
            "fullName": self.fullName,
            "dateOfBirth": self.dateOfBirth,
            "email": self.email,
            "phone": self.phone,
            "createdAt": self.createdAt,
        }

    @classmethod
    def from_dynamodb_item(cls, item: dict[str, Any]) -> Patient:
        """Create a Patient instance from a DynamoDB resource item dict."""
        return cls(
            patient_id=str(item["patient_id"]),
            fullName=str(item["fullName"]),
            dateOfBirth=str(item.get("dateOfBirth", "")),
            email=str(item["email"]),
            phone=str(item.get("phone", "")),
            createdAt=str(item["createdAt"]),
        )

    def to_api_response(self, *, self_access: bool = False, include_contact: bool = False) -> dict[str, Any]:
        """Return a dict safe for API responses with data minimization.

        Args:
            self_access: True when the caller is the owning patient.
            include_contact: True when authorized clinical/operational access requires contact info.
        """
        base: dict[str, Any] = {
            "patient_id": self.patient_id,
            "fullName": self.fullName,
            "dateOfBirth": self.dateOfBirth,
            "createdAt": self.createdAt,
        }
        if self_access or include_contact:
            base["email"] = self.email
            base["phone"] = self.phone
        return base
