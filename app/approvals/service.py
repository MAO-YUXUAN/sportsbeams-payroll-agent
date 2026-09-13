from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import ApprovalRecord, ApprovalStage, ApprovalStatus, now_iso
from .policy import requires_approval
from .validators import ApprovalError, validate_decision, validate_expense_month, validate_stage


class ApprovalService:
    """Store a run's approvals in audit/approvals.json without a database."""

    def __init__(self, run_directory: str | Path):
        self.run_directory = Path(run_directory).expanduser().resolve()
        self.audit_directory = self.run_directory / "audit"
        self.approvals_path = self.audit_directory / "approvals.json"

    def create(
        self,
        *,
        run_id: str,
        expense_month: str,
        stage: str | ApprovalStage,
        summary: str,
        requested_by: str = "agent",
        evidence_hash: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> ApprovalRecord:
        stage_value = stage.value if isinstance(stage, ApprovalStage) else stage
        validate_expense_month(expense_month)
        validate_stage(stage_value)
        if not requires_approval(stage_value):
            raise ApprovalError(f"Approval is not required for stage: {stage_value}")
        if not run_id.strip() or not summary.strip():
            raise ApprovalError("run_id and summary are required")
        records = self.list()
        existing = next(
            (
                record
                for record in records
                if record.run_id == run_id
                and record.stage == stage_value
                and record.status == ApprovalStatus.PENDING.value
            ),
            None,
        )
        if existing:
            return existing
        record = ApprovalRecord(
            id=f"apr_{uuid4().hex}",
            run_id=run_id,
            expense_month=expense_month,
            stage=stage_value,
            status=ApprovalStatus.PENDING.value,
            summary=summary,
            requested_at=now_iso(),
            requested_by=requested_by,
            evidence_hash=evidence_hash,
            details=details or {},
        )
        records.append(record)
        self._save(records)
        return record

    def approve(self, approval_id: str, *, decided_by: str, comment: str = "") -> ApprovalRecord:
        return self._decide(approval_id, ApprovalStatus.APPROVED, decided_by, comment)

    def reject(self, approval_id: str, *, decided_by: str, comment: str = "") -> ApprovalRecord:
        return self._decide(approval_id, ApprovalStatus.REJECTED, decided_by, comment)

    def get(self, approval_id: str) -> ApprovalRecord:
        record = next((item for item in self.list() if item.id == approval_id), None)
        if record is None:
            raise ApprovalError(f"Approval does not exist: {approval_id}")
        return record

    def list(self, *, status: str | ApprovalStatus | None = None) -> list[ApprovalRecord]:
        if not self.approvals_path.exists():
            return []
        try:
            raw = json.loads(self.approvals_path.read_text(encoding="utf-8"))
            records = [ApprovalRecord.from_dict(item) for item in raw.get("approvals", [])]
        except (json.JSONDecodeError, TypeError, KeyError) as error:
            raise ApprovalError(f"Invalid approvals file: {self.approvals_path}") from error
        if status is None:
            return records
        status_value = status.value if isinstance(status, ApprovalStatus) else status
        return [record for record in records if record.status == status_value]

    def is_approved(self, stage: str | ApprovalStage, *, evidence_hash: str | None = None) -> bool:
        stage_value = stage.value if isinstance(stage, ApprovalStage) else stage
        candidates = [
            record
            for record in self.list(status=ApprovalStatus.APPROVED)
            if record.stage == stage_value
        ]
        if evidence_hash is not None:
            candidates = [record for record in candidates if record.evidence_hash == evidence_hash]
        return bool(candidates)

    def _decide(
        self,
        approval_id: str,
        decision: ApprovalStatus,
        decided_by: str,
        comment: str,
    ) -> ApprovalRecord:
        records = self.list()
        record = next((item for item in records if item.id == approval_id), None)
        if record is None:
            raise ApprovalError(f"Approval does not exist: {approval_id}")
        validate_decision(record.status, decided_by)
        record.status = decision.value
        record.decided_at = now_iso()
        record.decided_by = decided_by.strip()
        record.comment = comment.strip()
        self._save(records)
        return record

    def _save(self, records: list[ApprovalRecord]) -> None:
        self.audit_directory.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "approvals": [record.to_dict() for record in records]}
        handle, temporary_name = tempfile.mkstemp(prefix="approvals-", suffix=".tmp", dir=self.audit_directory)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            temporary_path.replace(self.approvals_path)
        finally:
            temporary_path.unlink(missing_ok=True)
