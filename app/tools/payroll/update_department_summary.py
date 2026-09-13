from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from app.tools.excel.errors import ExcelInputError, ExcelOperationError
from app.tools.excel.models import ToolResult
from app.tools.excel.session import ExcelSession
from app.tools.excel.writer import save_as_xlsx, write_cell

from .vendor_models import SummaryMapping, VendorEmployeeCost, money


class DepartmentSummaryTool:
    """Preview or apply vendor costs to the payroll department-summary sheet."""

    def update(
        self,
        payroll_path: str | Path,
        records: list[VendorEmployeeCost],
        mapping: SummaryMapping,
        *,
        output_path: str | Path | None = None,
        allow_formula_overwrite: bool = False,
        dry_run: bool = True,
    ) -> ToolResult[dict]:
        changes, unmatched, duplicates, blocked = self._preview(
            payroll_path, records, mapping, allow_formula_overwrite=allow_formula_overwrite
        )
        result = {
            "status": "blocked" if unmatched or duplicates or blocked else "preview",
            "source_path": str(Path(payroll_path).resolve()),
            "output_path": str(Path(output_path).resolve()) if output_path else None,
            "changes": changes,
            "unmatched_employees": unmatched,
            "duplicate_matches": duplicates,
            "formula_cells_blocked": blocked,
        }
        if dry_run:
            return ToolResult(not (unmatched or duplicates or blocked), "update_department_summary", result)
        if unmatched or duplicates or blocked:
            raise ExcelOperationError("Department summary update is blocked by unresolved matches or formula cells")
        if output_path is None:
            raise ExcelInputError("output_path is required when dry_run is false")

        saved_path, _ = save_as_xlsx(payroll_path, output_path)
        try:
            with ExcelSession(saved_path, read_only=False) as session:
                sheet = session.sheet(mapping.sheet_name)
                for change in changes:
                    cell = sheet.Range(change["address"])
                    current = money(cell.Value2)
                    if current != Decimal(change["old_value"]):
                        raise ExcelOperationError(
                            f"Cell changed after preview: {mapping.sheet_name}!{change['address']}"
                        )
                    write_cell(session, mapping.sheet_name, change["address"], Decimal(change["new_value"]))
                session.workbook.Save()
            result["status"] = "completed"
            result["output_path"] = str(saved_path)
            return ToolResult(True, "update_department_summary", result)
        except Exception:
            saved_path.unlink(missing_ok=True)
            raise

    def read_summary(
        self,
        payroll_path: str | Path,
        records: list[VendorEmployeeCost],
        mapping: SummaryMapping,
    ) -> list[VendorEmployeeCost]:
        matched_records: list[VendorEmployeeCost] = []
        with ExcelSession(payroll_path, read_only=True) as session:
            sheet = session.sheet(mapping.sheet_name)
            index = self._build_index(sheet, mapping)
            for record in records:
                rows = index.get(self._record_key(record, mapping), [])
                if len(rows) != 1:
                    continue
                row = rows[0]
                values = {
                    field_name: money(sheet.Range(f"{column}{row}").Value2)
                    for field_name, column in mapping.field_columns.items()
                }
                matched_records.append(
                    VendorEmployeeCost(
                        vendor=record.vendor,
                        expense_month=record.expense_month,
                        employee_id=record.employee_id,
                        employee_name=record.employee_name,
                        **values,
                        source_file=str(Path(payroll_path).resolve()),
                        source_sheet=mapping.sheet_name,
                        source_row=row,
                    )
                )
        return matched_records

    def _preview(
        self,
        payroll_path: str | Path,
        records: list[VendorEmployeeCost],
        mapping: SummaryMapping,
        *,
        allow_formula_overwrite: bool,
    ) -> tuple[list[dict[str, Any]], list[dict], list[dict], list[dict]]:
        changes: list[dict[str, Any]] = []
        unmatched: list[dict] = []
        duplicates: list[dict] = []
        blocked: list[dict] = []
        with ExcelSession(payroll_path, read_only=True) as session:
            sheet = session.sheet(mapping.sheet_name)
            index = self._build_index(sheet, mapping)
            for record in records:
                record_key = self._record_key(record, mapping)
                rows = index.get(record_key, [])
                if not rows:
                    unmatched.append({"match_key": record_key, "employee_name": record.employee_name})
                    continue
                if len(rows) > 1:
                    duplicates.append({"match_key": record_key, "rows": rows})
                    continue
                row = rows[0]
                for field_name, column in mapping.field_columns.items():
                    new_value = record.amount(field_name)
                    cell = sheet.Range(f"{column}{row}")
                    if cell.HasFormula and not allow_formula_overwrite:
                        blocked.append(
                            {"employee_name": record.employee_name, "field": field_name, "address": f"{column}{row}"}
                        )
                        continue
                    old_value = money(cell.Value2)
                    if old_value != new_value:
                        changes.append(
                            {
                                "employee_name": record.employee_name,
                                "employee_id": record.employee_id,
                                "field": field_name,
                                "sheet": mapping.sheet_name,
                                "address": f"{column}{row}",
                                "old_value": str(old_value),
                                "new_value": str(new_value),
                            }
                        )
        return changes, unmatched, duplicates, blocked

    @staticmethod
    def _build_index(sheet, mapping: SummaryMapping) -> dict[str, list[int]]:
        index: dict[str, list[int]] = {}
        for row in range(mapping.start_row, mapping.end_row + 1):
            employee_id = (
                str(sheet.Range(f"{mapping.employee_id_column}{row}").Text).strip()
                if mapping.employee_id_column
                else ""
            )
            employee_name = str(sheet.Range(f"{mapping.employee_name_column}{row}").Text).strip()
            if not employee_id and not employee_name:
                continue
            expense_month = (
                str(sheet.Range(f"{mapping.expense_month_column}{row}").Text).strip()
                if mapping.expense_month_column
                else ""
            )
            identifier = employee_id if mapping.match_by == "employee_id" else employee_name
            prefix = "id" if mapping.match_by == "employee_id" else "name"
            key = f"{prefix}:{identifier}"
            if mapping.expense_month_column:
                key += f"|month:{expense_month}"
            index.setdefault(key, []).append(row)
        return index

    @staticmethod
    def _record_key(record: VendorEmployeeCost, mapping: SummaryMapping) -> str:
        identifier = record.employee_id.strip() if mapping.match_by == "employee_id" else record.employee_name.strip()
        prefix = "id" if mapping.match_by == "employee_id" else "name"
        key = f"{prefix}:{identifier}"
        if mapping.expense_month_column:
            key += f"|month:{record.expense_month.strip()}"
        return key
