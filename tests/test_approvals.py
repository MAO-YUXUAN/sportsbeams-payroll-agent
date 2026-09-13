import json

import pytest

from app.approvals import ApprovalError, ApprovalService, ApprovalStage, ApprovalStatus


def test_create_and_approve_request(tmp_path):
    service = ApprovalService(tmp_path / "2026-07" / "run-001")
    record = service.create(
        run_id="run-001",
        expense_month="2026-07",
        stage=ApprovalStage.PAYROLL_RECONCILIATION,
        summary="亚润与易才对账均通过，申请生成付款单",
        evidence_hash="sha256:abc",
        details={"payment_total": "68013.55"},
    )

    approved = service.approve(record.id, decided_by="张经理", comment="金额确认无误")

    assert approved.status == ApprovalStatus.APPROVED.value
    assert service.is_approved(ApprovalStage.PAYROLL_RECONCILIATION, evidence_hash="sha256:abc")
    saved = json.loads(service.approvals_path.read_text(encoding="utf-8"))
    assert saved["approvals"][0]["decided_by"] == "张经理"


def test_duplicate_pending_request_is_idempotent(tmp_path):
    service = ApprovalService(tmp_path / "run-001")
    first = service.create(
        run_id="run-001", expense_month="2026-07", stage="vendor_import", summary="确认导入"
    )
    second = service.create(
        run_id="run-001", expense_month="2026-07", stage="vendor_import", summary="再次提交"
    )

    assert first.id == second.id
    assert len(service.list(status=ApprovalStatus.PENDING)) == 1


def test_approval_cannot_be_decided_twice(tmp_path):
    service = ApprovalService(tmp_path / "run-001")
    record = service.create(
        run_id="run-001", expense_month="2026-07", stage="payment_request", summary="确认付款金额"
    )
    service.reject(record.id, decided_by="复核人", comment="金额有误")

    with pytest.raises(ApprovalError):
        service.approve(record.id, decided_by="另一复核人")
