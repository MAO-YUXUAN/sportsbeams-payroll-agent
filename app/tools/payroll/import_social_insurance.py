from __future__ import annotations

from pathlib import Path

from app.tools.excel.errors import ExcelInputError
from app.tools.excel.models import ToolResult
from app.tools.excel.session import ExcelSession

from .records import EmployeeAmountRecord
from .vendor_models import money


class SocialInsuranceImportTool:
    """Extract the internal social-insurance detail workbook (file 19)."""

    def extract(self, workbook_path: str | Path, *, expense_month: str, sheet_name: str = "赛倍明") -> ToolResult[dict]:
        records = []
        with ExcelSession(workbook_path, read_only=True) as session:
            sheet = session.sheet(sheet_name)
            final_row = int(sheet.UsedRange.Row + sheet.UsedRange.Rows.Count - 1)
            for row in range(4, final_row + 1):
                name = str(sheet.Range(f"C{row}").Text).strip()
                if not name:
                    continue
                sequence = str(sheet.Range(f"A{row}").Text).strip()
                if sequence in {"小计", "合计", "总计"}:
                    continue
                values = {
                    "social_base": money(sheet.Range(f"E{row}").Value2),
                    "pension_company": money(sheet.Range(f"F{row}").Value2),
                    "medical_company": money(sheet.Range(f"G{row}").Value2),
                    "unemployment_company": money(sheet.Range(f"H{row}").Value2),
                    "injury_company": money(sheet.Range(f"I{row}").Value2),
                    "supplemental_medical_company": money(sheet.Range(f"J{row}").Value2),
                    "pension_employee": money(sheet.Range(f"K{row}").Value2),
                    "medical_employee": money(sheet.Range(f"L{row}").Value2),
                    "unemployment_employee": money(sheet.Range(f"M{row}").Value2),
                    "social_total": money(sheet.Range(f"N{row}").Value2),
                }
                records.append(EmployeeAmountRecord(name, expense_month, values, department=str(sheet.Range(f"B{row}").Text).strip(), source_file=str(Path(workbook_path).resolve()), source_sheet=sheet_name, source_row=row))
        if not records:
            raise ExcelInputError("No social-insurance records were found")
        total = sum((record.amount("social_total") for record in records), money(0))
        return ToolResult(True, "import_social_insurance", {"record_count": len(records), "total": str(total), "records": [r.to_dict() for r in records], "record_objects": records})
