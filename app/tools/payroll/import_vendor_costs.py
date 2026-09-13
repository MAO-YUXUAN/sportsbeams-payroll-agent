from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.tools.excel.errors import ExcelInputError
from app.tools.excel.models import ToolResult
from app.tools.excel.session import ExcelSession

from .vendor_models import AMOUNT_FIELDS, SettlementMapping, VendorEmployeeCost, money


class VendorCostImportTool:
    """Extract a vendor workbook into the canonical employee-cost model."""

    def extract(
        self,
        settlement_path: str | Path,
        *,
        vendor: str,
        expense_month: str,
        mapping: SettlementMapping,
    ) -> ToolResult[dict]:
        required_columns = {"employee_name"}
        missing = required_columns.difference(mapping.columns)
        if missing:
            raise ExcelInputError(f"Settlement mapping is missing fields: {sorted(missing)}")

        records: list[VendorEmployeeCost] = []
        warnings: list[str] = []
        with ExcelSession(settlement_path, read_only=True) as session:
            sheet = session.sheet(mapping.sheet_name)
            final_row = mapping.end_row or int(sheet.UsedRange.Row + sheet.UsedRange.Rows.Count - 1)
            blank_rows = 0
            for row in range(mapping.start_row, final_row + 1):
                employee_name = self._text(sheet, mapping.columns["employee_name"], row)
                employee_id = self._text(sheet, mapping.columns.get("employee_id"), row)
                if not employee_name and not employee_id:
                    blank_rows += 1
                    if mapping.end_row is None and blank_rows >= mapping.blank_row_limit:
                        break
                    continue
                blank_rows = 0
                if employee_name.strip() in mapping.excluded_names:
                    continue

                amounts = {}
                for field_name in AMOUNT_FIELDS:
                    if field_name in mapping.sum_columns:
                        amounts[field_name] = sum(
                            (self._amount(sheet, column, row) for column in mapping.sum_columns[field_name]),
                            Decimal("0.00"),
                        )
                    else:
                        amounts[field_name] = self._amount(sheet, mapping.columns.get(field_name), row)
                row_expense_month = self._text(sheet, mapping.columns.get("expense_month"), row) or expense_month
                record = VendorEmployeeCost(
                    vendor=vendor,
                    expense_month=row_expense_month,
                    employee_id=employee_id,
                    employee_name=employee_name,
                    **amounts,
                    source_file=str(Path(settlement_path).resolve()),
                    source_sheet=mapping.sheet_name,
                    source_row=row,
                )
                if record.row_total and record.row_total != record.calculated_total:
                    warnings.append(
                        f"{record.employee_name or record.employee_id} row {row}: "
                        f"provided total {record.row_total} differs from components {record.calculated_total}"
                    )
                records.append(record)

            reported_total = None
            if mapping.total_cell:
                reported_total = money(sheet.Range(mapping.total_cell).Value2)

        if not records:
            raise ExcelInputError("No employee records were extracted from the settlement workbook")

        component_total = sum((record.calculated_total for record in records), Decimal("0.00"))
        row_total = sum((record.row_total for record in records), Decimal("0.00"))
        return ToolResult(
            True,
            "import_vendor_costs",
            {
                "vendor": vendor,
                "expense_month": expense_month,
                "record_count": len(records),
                "records": [record.to_dict() for record in records],
                "component_total": str(component_total),
                "row_total": str(row_total),
                "reported_total": str(reported_total) if reported_total is not None else None,
                "record_objects": records,
            },
            warnings,
        )

    @staticmethod
    def _text(sheet, column: str | None, row: int) -> str:
        if not column:
            return ""
        return str(sheet.Range(f"{column}{row}").Text).strip()

    @staticmethod
    def _amount(sheet, column: str | None, row: int) -> Decimal:
        if not column:
            return Decimal("0.00")
        return money(sheet.Range(f"{column}{row}").Value2)
