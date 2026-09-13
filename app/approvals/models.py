from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ApprovalStage(str, Enum):
    VENDOR_IMPORT = "vendor_import"
    PAYROLL_RECONCILIATION = "payroll_reconciliation"
    PAYMENT_REQUEST = "payment_request"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(slots=True)
class ApprovalRecord:
    id: str
    run_id: str
    expense_month: str
    stage: str
    status: str
    summary: str
    requested_at: str
    requested_by: str = "agent"
    evidence_hash: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    decided_at: str | None = None
    decided_by: str | None = None
    comment: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ApprovalRecord":
        return cls(**value)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
