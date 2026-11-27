"""AuditEvent model for SecureCare backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AuditEvent:
    """Represents an immutable audit-trail entry in securecare-audit.

    Attributes:
        patient_id: Owning patient's identifier or 'SYSTEM' (Partition Key).
        event_id: Unique event identifier (Sort Key).
        day: UTC date string (YYYY-MM-DD) used for ByDay GSI PK.
        timestamp: ISO-8601 UTC timestamp.
        actor_id: Cognito sub of the actor.
        actor_role: Role of the actor (patient, doctor, staff, admin).
        action: Machine-readable action name.
        outcome: Result of the action (ALLOWED or DENIED).
        source_ip: Originating source IP address.
        request_id: Correlation request ID.
        expiresAt: Numeric Unix epoch timestamp for future TTL retention.
        resource_type: Optional resource type.
        resource_id: Optional resource identifier.
        detail: Optional sanitized metadata dict.
    """

    patient_id: str
    event_id: str
    day: str
    timestamp: str
    actor_id: str
    actor_role: str
    action: str
    outcome: str
    source_ip: str
    request_id: str
    expiresAt: int
    resource_type: str | None = field(default=None)
    resource_id: str | None = field(default=None)
    detail: dict[str, Any] | None = field(default=None)

    def to_dynamodb_item(self) -> dict[str, Any]:
        """Convert to a DynamoDB resource item dict matching exact schema."""
        item: dict[str, Any] = {
            "patient_id": self.patient_id,
            "event_id": self.event_id,
            "day": self.day,
            "timestamp": self.timestamp,
            "actor_id": self.actor_id,
            "actor_role": self.actor_role,
            "action": self.action,
            "outcome": self.outcome,
            "source_ip": self.source_ip,
            "request_id": self.request_id,
            "expiresAt": int(self.expiresAt),
        }
        if self.resource_type is not None:
            item["resource_type"] = self.resource_type
        if self.resource_id is not None:
            item["resource_id"] = self.resource_id
        if self.detail is not None:
            item["detail"] = self.detail
        return item

    @classmethod
    def from_dynamodb_item(cls, item: dict[str, Any]) -> AuditEvent:
        """Create an AuditEvent instance from a DynamoDB resource item dict."""
        return cls(
            patient_id=str(item["patient_id"]),
            event_id=str(item["event_id"]),
            day=str(item["day"]),
            timestamp=str(item["timestamp"]),
            actor_id=str(item["actor_id"]),
            actor_role=str(item["actor_role"]),
            action=str(item["action"]),
            outcome=str(item["outcome"]),
            source_ip=str(item["source_ip"]),
            request_id=str(item["request_id"]),
            expiresAt=int(item["expiresAt"]),
            resource_type=str(item["resource_type"]) if item.get("resource_type") is not None else None,
            resource_id=str(item["resource_id"]) if item.get("resource_id") is not None else None,
            detail=dict(item["detail"]) if isinstance(item.get("detail"), dict) else None,
        )

    def to_api_response(self) -> dict[str, Any]:
        """Return a dict for API responses."""
        resp: dict[str, Any] = {
            "patient_id": self.patient_id,
            "event_id": self.event_id,
            "day": self.day,
            "timestamp": self.timestamp,
            "actor_id": self.actor_id,
            "actor_role": self.actor_role,
            "action": self.action,
            "outcome": self.outcome,
            "source_ip": self.source_ip,
            "request_id": self.request_id,
            "expiresAt": self.expiresAt,
        }
        if self.resource_type is not None:
            resp["resource_type"] = self.resource_type
        if self.resource_id is not None:
            resp["resource_id"] = self.resource_id
        if self.detail is not None:
            resp["detail"] = self.detail
        return resp
