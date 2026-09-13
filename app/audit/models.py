from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class AuditEvent:
    id: str
    sequence: int
    timestamp: str
    run_id: str
    event_type: str
    actor: str
    status: str
    details: dict[str, Any] = field(default_factory=dict)
    previous_hash: str = ""
    event_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AuditEvent":
        return cls(**value)


@dataclass(slots=True)
class AuditVerification:
    valid: bool
    event_count: int
    errors: list[str] = field(default_factory=list)
