from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from app.tools.excel.errors import ExcelInputError, ExcelOperationError
from app.tools.excel.models import ToolResult
from app.tools.excel.writer import sha256_file


class RunFilePreparationTool:
    """Copy validated source files into an isolated month run and write a manifest."""

    def prepare(
        self,
        validation_result: ToolResult[dict],
        runs_root: str | Path,
        *,
        expense_month: str,
        run_id: str | None = None,
    ) -> ToolResult[dict]:
        if not validation_result.success or validation_result.data.get("status") != "passed":
            raise ExcelOperationError("Run preparation requires a passed input-set validation")
        run_root = Path(runs_root).expanduser().resolve() / expense_month
        if run_id:
            if Path(run_id).name != run_id or run_id in {".", ".."}:
                raise ExcelInputError("run_id must be a safe directory name")
            run_root = run_root / run_id
        input_root = run_root / "input"
        directories = [
            input_root,
            run_root / "working",
            run_root / "output" / "payroll",
            run_root / "output" / "payment_requests",
            run_root / "output" / "reports",
            run_root / "audit",
        ]
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

        manifest_files = []
        for category_id, items in validation_result.data["files_by_category"].items():
            for item in items:
                destination_name = item.get("destination")
                if not destination_name:
                    raise ExcelInputError(f"Missing destination for category {category_id}")
                destination_dir = (input_root / destination_name).resolve()
                if input_root not in destination_dir.parents:
                    raise ExcelInputError(f"Unsafe input destination: {destination_name}")
                destination_dir.mkdir(parents=True, exist_ok=True)
                source = Path(item["path"]).resolve()
                destination = destination_dir / source.name
                if destination.exists() and sha256_file(destination) != item["sha256"]:
                    raise ExcelOperationError(f"A different file already exists in the run: {destination}")
                if not destination.exists():
                    shutil.copy2(source, destination)
                if sha256_file(destination) != item["sha256"]:
                    destination.unlink(missing_ok=True)
                    raise ExcelOperationError(f"Copied file hash mismatch: {source.name}")
                manifest_files.append(
                    {
                        "category_id": category_id,
                        "source_path": str(source),
                        "stored_path": str(destination),
                        "sha256": item["sha256"],
                    }
                )
        manifest = {
            "expense_month": expense_month,
            "run_id": run_id,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "files": manifest_files,
        }
        manifest_path = run_root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return ToolResult(
            True,
            "prepare_run_files",
            {
                "status": "prepared",
                "run_root": str(run_root),
                "input_root": str(input_root),
                "working_root": str(run_root / "working"),
                "output_root": str(run_root / "output"),
                "manifest_path": str(manifest_path),
                "file_count": len(manifest_files),
            },
        )
