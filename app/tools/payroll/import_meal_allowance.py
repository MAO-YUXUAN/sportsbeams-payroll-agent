from __future__ import annotations

from pathlib import Path

from app.tools.excel.errors import ExcelInputError
from app.tools.excel.models import ToolResult
from app.tools.excel.session import ExcelSession

from .records import EmployeeAmountRecord
from .vendor_models import money


class MealAllowanceImportTool:
    def extract(self, workbook_path: str | Path, *, expense_month: str) -> ToolResult[dict]:
        month = int(expense_month.split("-")[1])
        sheet_name = f"{month}月"
        records = []
        with ExcelSession(workbook_path, read_only=True) as session:
            sheet = session.sheet(sheet_name)
            last_row = int(sheet.UsedRange.Row + sheet.UsedRange.Rows.Count - 1)
            for row in range(3, last_row + 1):
                name = str(sheet.Range(f"C{row}").Text).strip()
                if not name or name in {"合计", "总计", "小计"}:
                    continue
                values = {
                    "attendance_meal": money(sheet.Range(f"D{row}").Value2),
                    "absence_deduction": money(sheet.Range(f"E{row}").Value2),
                    "overtime_allowance": money(sheet.Range(f"F{row}").Value2),
                    "hospitality_allowance": money(sheet.Range(f"G{row}").Value2),
                    "meal_total": money(sheet.Range(f"H{row}").Value2),
                }
                records.append(EmployeeAmountRecord(name, expense_month, values, department=str(sheet.Range(f"B{row}").Text).strip(), source_file=str(Path(workbook_path).resolve()), source_sheet=sheet_name, source_row=row))
        if not records:
            raise ExcelInputError("No meal-allowance records were found")
        total = sum((record.amount("meal_total") for record in records), money(0))
        return ToolResult(True, "import_meal_allowance", {"record_count": len(records), "total": str(total), "records": [r.to_dict() for r in records], "record_objects": records})
