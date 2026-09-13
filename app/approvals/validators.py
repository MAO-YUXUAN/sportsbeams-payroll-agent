from __future__ import annotations

from datetime import date

from .models import ApprovalStage, ApprovalStatus


class ApprovalError(RuntimeError):
    """Raised when an approval request or state transition is invalid."""


def validate_expense_month(expense_month: str) -> None:
    try:
        year, month = (int(part) for part in expense_month.split("-", 1))
        date(year, month, 1)
    except (TypeError, ValueError) as error:
        raise ApprovalError("expense_month must use YYYY-MM format") from error


def validate_stage(stage: str) -> None:
    if stage not in {item.value for item in ApprovalStage}:
        raise ApprovalError(f"Unsupported approval stage: {stage}")


def validate_decision(status: str, decided_by: str) -> None:
    if status != ApprovalStatus.PENDING.value:
        raise ApprovalError(f"Only pending approvals can be decided; current status is {status}")
    if not decided_by.strip():
        raise ApprovalError("decided_by is required")
