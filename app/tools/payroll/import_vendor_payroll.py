from __future__ import annotations

from pathlib import Path

from app.tools.excel.errors import ExcelInputError
from app.tools.excel.models import ToolResult
from app.tools.excel.session import ExcelSession

from .records import EmployeeAmountRecord
from .vendor_models import money


class VendorPayrollImportTool:
    """Extract actual Yicai-dispatch or Yarun payroll workbooks."""

    def extract(self, workbook_path: str | Path, *, vendor: str, expense_month: str) -> ToolResult[dict]:
        month = int(expense_month.split("-")[1])
        is_yicai = vendor.casefold() in {"yicai", "易才"}
        sheet_name = f"{month}月派遣" if is_yicai else f"{month}月"
        start_row = 4 if is_yicai else 5
        name_col = "C" if is_yicai else "B"
        id_col = "" if is_yicai else "C"
        mapping = (
            {"gross_pay": "P", "personal_social_housing": "AE", "income_tax": "AI", "net_pay": "AJ"}
            if is_yicai
            else {"gross_pay": "D", "personal_social_housing": "I", "income_tax": "K", "net_pay": "L", "service_fee": "M"}
        )
        records = []
        with ExcelSession(workbook_path, read_only=True) as session:
            sheet = session.sheet(sheet_name)
            final_row = int(sheet.UsedRange.Row + sheet.UsedRange.Rows.Count - 1)
            blank_count = 0
            for row in range(start_row, final_row + 1):
                name = str(sheet.Range(f"{name_col}{row}").Text).strip()
                if not name:
                    blank_count += 1
                    if blank_count >= 2:
                        break
                    continue
                blank_count = 0
                if name in {"合计", "汇总", "总计", "姓名"}:
                    break
                records.append(EmployeeAmountRecord(name, expense_month, {key: money(sheet.Range(f"{col}{row}").Value2) for key, col in mapping.items()}, employee_id=str(sheet.Range(f"{id_col}{row}").Text).strip() if id_col else "", source_file=str(Path(workbook_path).resolve()), source_sheet=sheet_name, source_row=row))
        if not records:
            raise ExcelInputError(f"No {vendor} payroll records were found")
        return ToolResult(True, "import_vendor_payroll", {"vendor": vendor, "record_count": len(records), "records": [r.to_dict() for r in records], "record_objects": records})
