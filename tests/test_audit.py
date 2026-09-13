import json

import pytest

from app.audit import AuditError, AuditLogger


def test_log_read_and_verify_hash_chain(tmp_path):
    logger = AuditLogger(tmp_path / "run-001", run_id="run-001")
    first = logger.log("run_started", actor="user", status="started")
    second = logger.log(
        "reconciliation_completed",
        status="success",
        details={"difference_count": 0, "amount": "68013.55"},
    )

    assert second.sequence == 2
    assert second.previous_hash == first.event_hash
    assert logger.verify().valid
    assert len(logger.read(status="success")) == 1


def test_sensitive_details_are_redacted(tmp_path):
    logger = AuditLogger(tmp_path / "run-001", run_id="run-001")
    event = logger.log(
        "file_imported",
        details={"employee_id": "310000000000000000", "nested": {"api_key": "secret"}},
    )

    assert event.details["employee_id"] == "[REDACTED]"
    assert event.details["nested"]["api_key"] == "[REDACTED]"


def test_verify_detects_tampering(tmp_path):
    logger = AuditLogger(tmp_path / "run-001", run_id="run-001")
    logger.log("run_started", status="started")
    event = logger.log("run_completed", status="success")
    lines = logger.events_path.read_text(encoding="utf-8").splitlines()
    altered = json.loads(lines[1])
    altered["status"] = "failed"
    lines[1] = json.dumps(altered, ensure_ascii=False, separators=(",", ":"))
    logger.events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    verification = logger.verify()

    assert not verification.valid
    assert any("hash does not match" in error for error in verification.errors)
    assert event.id in verification.errors[0] or len(verification.errors) > 0


def test_invalid_status_is_rejected(tmp_path):
    logger = AuditLogger(tmp_path / "run-001", run_id="run-001")
    with pytest.raises(AuditError):
        logger.log("run_started", status="unknown")


def test_blocked_status_is_recorded_for_business_rule_failures(tmp_path):
    logger = AuditLogger(tmp_path / "run-001", run_id="run-001")

    event = logger.log(
        "payment_request_blocked",
        status="blocked",
        details={"reason": "reconciliation_failed"},
    )

    assert event.status == "blocked"
    assert logger.read(status="blocked") == [event]
    assert logger.verify().valid
