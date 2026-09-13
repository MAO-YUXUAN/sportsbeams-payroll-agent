from __future__ import annotations

from .models import ApprovalStage


REQUIRED_APPROVAL_STAGES = {
    ApprovalStage.VENDOR_IMPORT.value,
    ApprovalStage.PAYROLL_RECONCILIATION.value,
    ApprovalStage.PAYMENT_REQUEST.value,
}


def requires_approval(stage: str | ApprovalStage) -> bool:
    value = stage.value if isinstance(stage, ApprovalStage) else stage
    return value in REQUIRED_APPROVAL_STAGES
