from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .state import WorkflowState, ensure_transition


@dataclass(slots=True)
class AgentContext:
    run_id: str
    expense_month: str
    source_directory: str
    run_directory: str
    state: str = WorkflowState.CREATED.value
    files: dict[str, list[str]] = field(default_factory=dict)
    summaries: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    approval_evidence: dict[str, str] = field(default_factory=dict)
    last_error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))
    updated_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))

    @property
    def current_state(self) -> WorkflowState:
        return WorkflowState(self.state)

    @property
    def state_path(self) -> Path:
        return Path(self.run_directory) / "state.json"

    def transition(self, target: WorkflowState) -> None:
        ensure_transition(self.current_state, target)
        self.state = target.value
        self.updated_at = datetime.now().astimezone().isoformat(timespec="seconds")

    def save(self) -> None:
        path = self.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(prefix="state-", suffix=".tmp", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(asdict(self), stream, ensure_ascii=False, indent=2, default=str)
                stream.flush(); os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @classmethod
    def load(cls, run_directory: str | Path) -> "AgentContext":
        path = Path(run_directory).expanduser().resolve() / "state.json"
        return cls(**json.loads(path.read_text(encoding="utf-8")))
