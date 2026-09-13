from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import AuditEvent, AuditVerification


ALLOWED_STATUSES = {
    "started",
    "success",
    "failed",
    "blocked",
    "pending",
    "approved",
    "rejected",
    "info",
}
SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "bank_account",
    "bank_card",
    "card_number",
    "employee_id",
    "id_card",
    "identity_number",
    "password",
    "prompt",
    "secret",
    "token",
}


class AuditError(RuntimeError):
    """Raised when an audit event or audit file is invalid."""


class AuditLogger:
    """Append privacy-filtered, hash-chained events to audit/events.jsonl."""

    def __init__(self, run_directory: str | Path, *, run_id: str):
        self.run_directory = Path(run_directory).expanduser().resolve()
        self.run_id = run_id.strip()
        if not self.run_id:
            raise AuditError("run_id is required")
        self.audit_directory = self.run_directory / "audit"
        self.events_path = self.audit_directory / "events.jsonl"

    def log(
        self,
        event_type: str,
        *,
        actor: str = "agent",
        status: str = "info",
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        if not event_type.strip() or not actor.strip():
            raise AuditError("event_type and actor are required")
        if status not in ALLOWED_STATUSES:
            raise AuditError(f"Unsupported audit status: {status}")
        existing = self.read()
        previous_hash = existing[-1].event_hash if existing else ""
        event = AuditEvent(
            id=f"evt_{uuid4().hex}",
            sequence=len(existing) + 1,
            timestamp=datetime.now().astimezone().isoformat(timespec="milliseconds"),
            run_id=self.run_id,
            event_type=event_type.strip(),
            actor=actor.strip(),
            status=status,
            details=_sanitize(details or {}),
            previous_hash=previous_hash,
        )
        event.event_hash = _calculate_hash(event)
        self.audit_directory.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"
        with self.events_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        return event

    def read(
        self,
        *,
        event_type: str | None = None,
        status: str | None = None,
    ) -> list[AuditEvent]:
        if not self.events_path.exists():
            return []
        records = []
        for line_number, line in enumerate(self.events_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                records.append(AuditEvent.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError) as error:
                raise AuditError(f"Invalid audit event at line {line_number}") from error
        if event_type is not None:
            records = [record for record in records if record.event_type == event_type]
        if status is not None:
            records = [record for record in records if record.status == status]
        return records

    def verify(self) -> AuditVerification:
        try:
            records = self.read()
        except AuditError as error:
            return AuditVerification(False, 0, [str(error)])
        errors = []
        previous_hash = ""
        for expected_sequence, event in enumerate(records, start=1):
            if event.sequence != expected_sequence:
                errors.append(f"Event {event.id} has invalid sequence {event.sequence}")
            if event.run_id != self.run_id:
                errors.append(f"Event {event.id} belongs to a different run")
            if event.previous_hash != previous_hash:
                errors.append(f"Event {event.id} has a broken previous_hash")
            if event.event_hash != _calculate_hash(event):
                errors.append(f"Event {event.id} hash does not match its content")
            previous_hash = event.event_hash
        return AuditVerification(not errors, len(records), errors)


def _sanitize(value: Any, *, key: str = "") -> Any:
    normalized_key = key.casefold().replace("-", "_")
    if normalized_key in SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): _sanitize(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _calculate_hash(event: AuditEvent) -> str:
    value = event.to_dict()
    value["event_hash"] = ""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
