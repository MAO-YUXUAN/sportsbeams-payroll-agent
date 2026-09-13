from __future__ import annotations

from pathlib import Path

from app.tools.excel.models import ToolResult
from app.tools.excel.session import ExcelSession, SUPPORTED_EXTENSIONS
from app.tools.excel.validator import validate_workbook
from app.tools.excel.writer import sha256_file


class OutputValidationTool:
    def validate(self, files: list[dict]) -> ToolResult[dict]:
        checked, errors = [], []
        for specification in files:
            path = Path(specification["path"]).expanduser().resolve()
            if not path.is_file() or path.stat().st_size == 0:
                errors.append({"path": str(path), "error": "missing_or_empty"}); continue
            item = {"path": str(path), "size": path.stat().st_size, "sha256": sha256_file(path)}
            if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                try:
                    with ExcelSession(path, read_only=True) as session:
                        validation = validate_workbook(session, list(specification.get("required_sheets", [])))
                    item["workbook_valid"] = validation.valid
                    if not validation.valid:
                        errors.append({"path": str(path), "missing_sheets": validation.required_sheets_missing, "formula_error_count": len(validation.formula_errors)})
                except Exception as error:
                    errors.append({"path": str(path), "error": str(error)})
            checked.append(item)
        return ToolResult(not errors, "validate_outputs", {"status": "passed" if not errors else "blocked", "checked": checked, "errors": errors})
