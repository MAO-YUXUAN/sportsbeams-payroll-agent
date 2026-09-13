from __future__ import annotations

from pathlib import Path

from app.tools.excel.errors import ExcelInputError
from app.tools.excel.models import ToolResult
from app.tools.excel.session import ExcelSession

from .records import EmployeeAmountRecord
from .vendor_models import money


class HousingFundImportTool:
    def extract(self, workbook_path: str | Path, *, expense_month: str, sheet_name: str = "赛倍明") -> ToolResult[dict]:
        records = []
        with ExcelSession(workbook_path, read_only=True) as session:
            sheet = session.sheet(sheet_name)
            final_row = int(sheet.UsedRange.Row + sheet.UsedRange.Rows.Count - 1)
            for row in range(4, final_row + 1):
                name = str(sheet.Range(f"C{row}").Text).strip()
                sequence = str(sheet.Range(f"A{row}").Text).strip()
                if not name or sequence in {"小计", "合计", "总计", "总合计"}:
                    continue
                values = {
                    "housing_base": money(sheet.Range(f"E{row}").Value2),
                    "housing_company": money(sheet.Range(f"F{row}").Value2),
                    "housing_employee": money(sheet.Range(f"G{row}").Value2),
                    "housing_total": money(sheet.Range(f"H{row}").Value2),
                }
                records.append(EmployeeAmountRecord(name, expense_month, values, department=str(sheet.Range(f"B{row}").Text).strip(), source_file=str(Path(workbook_path).resolve()), source_sheet=sheet_name, source_row=row))
        if not records:
            raise ExcelInputError("No housing-fund records were found")
        total = sum((record.amount("housing_total") for record in records), money(0))
        return ToolResult(True, "import_housing_fund", {"record_count": len(records), "total": str(total), "records": [r.to_dict() for r in records], "record_objects": records})
