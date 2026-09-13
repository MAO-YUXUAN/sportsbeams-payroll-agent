from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.tools.excel.errors import ExcelInputError, ExcelOperationError
from app.tools.excel.models import ToolResult
from app.tools.excel.session import ExcelSession
from app.tools.excel.writer import save_as_xlsx, write_cell

from .records import EmployeeAmountRecord
from .vendor_models import money


class EmployeePayrollUpdateTool:
    """Preview/apply configured employee fields; business mappings stay outside code."""

    def update(self, payroll_path: str | Path, records: list[EmployeeAmountRecord], *, sheet_name: str, start_row: int, end_row: int, employee_name_column: str, field_columns: dict[str, str], name_aliases: dict[str, str] | None = None, output_path: str | Path | None = None, allow_formula_overwrite: bool = False, dry_run: bool = True) -> ToolResult[dict]:
        aliases = name_aliases or {}
        with ExcelSession(payroll_path, read_only=True) as session:
            sheet = session.sheet(sheet_name)
            index = {}
            for row in range(start_row, end_row + 1):
                name = str(sheet.Range(f"{employee_name_column}{row}").Text).strip()
                if name:
                    index.setdefault(name, []).append(row)
            changes, unmatched, duplicates = [], [], []
            for record in records:
                target_name = aliases.get(record.employee_name, record.employee_name)
                rows = index.get(target_name, [])
                if not rows:
                    unmatched.append(record.employee_name); continue
                if len(rows) > 1:
                    duplicates.append({"employee_name": target_name, "rows": rows}); continue
                row = rows[0]
                for field, column in field_columns.items():
                    cell = sheet.Range(f"{column}{row}")
                    new = record.amount(field); old = money(cell.Value2)
                    if old != new:
                        changes.append({"employee_name": target_name, "field": field, "address": f"{column}{row}", "old_value": str(old), "new_value": str(new), "has_formula": bool(cell.HasFormula)})
        blocked = unmatched or duplicates or (
            not allow_formula_overwrite and any(change["has_formula"] for change in changes)
        )
        result = {"status": "blocked" if blocked else ("preview" if dry_run else "executing"), "changes": changes, "unmatched": unmatched, "duplicates": duplicates}
        if dry_run:
            return ToolResult(not blocked, "update_employee_payroll_fields", result)
        if blocked:
            raise ExcelOperationError("Employee payroll update is blocked")
        if output_path is None:
            raise ExcelInputError("output_path is required when dry_run is false")
        source = Path(payroll_path).expanduser().resolve()
        destination = Path(output_path).expanduser().resolve()
        if source == destination:
            saved = source
        else:
            saved, _ = save_as_xlsx(source, destination)
        with ExcelSession(saved, read_only=False) as session:
            for change in changes:
                write_cell(session, sheet_name, change["address"], Decimal(change["new_value"]))
            session.app.CalculateFull()
            session.workbook.Save()
        result.update({"status": "completed", "output_path": str(saved)})
        return ToolResult(True, "update_employee_payroll_fields", result)
