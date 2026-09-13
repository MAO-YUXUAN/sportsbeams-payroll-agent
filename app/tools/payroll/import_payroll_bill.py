from __future__ import annotations

from pathlib import Path

from app.tools.excel.errors import ExcelInputError
from app.tools.excel.models import ToolResult
from app.tools.excel.session import ExcelSession

from .records import EmployeeAmountRecord
from .vendor_models import money


class PayrollBillImportTool:
    """Extract employee amounts from the real Yicai (17) or Yarun (18) bill."""

    def extract(self, workbook_path: str | Path, *, bill_type: str, expense_month: str) -> ToolResult[dict]:
        yicai = bill_type.casefold() in {"yicai", "易才"}
        sheet_name = "1" if yicai else expense_month.replace("-", "") + "工资"
        start_row = 8 if yicai else 9
        mapping = (
            {"name": "D", "id": "F", "month": "H", "gross_pay": "J", "net_pay": "M", "income_tax": "N"}
            if yicai
            else {"name": "C", "id": "D", "gross_pay": "F", "personal_social_housing": "K", "income_tax": "O", "net_pay": "P"}
        )
        records = []
        with ExcelSession(workbook_path, read_only=True) as session:
            sheet = session.sheet(sheet_name)
            final_row = int(sheet.UsedRange.Row + sheet.UsedRange.Rows.Count - 1)
            for row in range(start_row, final_row + 1):
                name = str(sheet.Range(f"{mapping['name']}{row}").Text).strip()
                if not name or name in {"合计", "总计", "汇总"}:
                    if records:
                        break
                    continue
                row_month = str(sheet.Range(f"{mapping['month']}{row}").Text).strip() if "month" in mapping else expense_month
                values = {key: money(sheet.Range(f"{column}{row}").Value2) for key, column in mapping.items() if key not in {"name", "id", "month"}}
                if yicai:
                    values["personal_social_housing"] = sum((money(sheet.Cells(row, column).Value2) for column in range(21, 25)), money(0))
                records.append(EmployeeAmountRecord(name, row_month, values, employee_id=str(sheet.Range(f"{mapping['id']}{row}").Text).strip(), source_file=str(Path(workbook_path).resolve()), source_sheet=sheet_name, source_row=row))
        if not records:
            raise ExcelInputError(f"No {bill_type} payroll-bill records were found")
        return ToolResult(True, "import_payroll_bill", {"bill_type": bill_type, "record_count": len(records), "records": [r.to_dict() for r in records], "record_objects": records})
