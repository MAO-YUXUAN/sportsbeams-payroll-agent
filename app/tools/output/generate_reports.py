from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from app.tools.excel.models import ToolResult


class ReconciliationReportTool:
    """Generate an auditable JSON reconciliation/exception report."""

    def generate(self, output_path: str | Path, *, run_id: str, expense_month: str, results: dict[str, Any]) -> ToolResult[dict]:
        destination = Path(output_path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        checks = []
        for name, result in results.items():
            if hasattr(result, "to_dict"):
                result = result.to_dict()
            success = bool(result.get("success", False)) if isinstance(result, dict) else False
            checks.append({"name": name, "success": success, "result": result})
        payload = {"version": 1, "run_id": run_id, "expense_month": expense_month, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "status": "passed" if all(item["success"] for item in checks) else "blocked", "checks": checks}
        handle, temporary_name = tempfile.mkstemp(prefix="report-", suffix=".tmp", dir=destination.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2, default=str)
                stream.flush(); os.fsync(stream.fileno())
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return ToolResult(payload["status"] == "passed", "generate_reconciliation_report", {"status": payload["status"], "output_path": str(destination), "check_count": len(checks)})
