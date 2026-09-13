from __future__ import annotations

from pathlib import Path

from app.tools.excel.errors import ExcelInputError
from app.tools.excel.models import ToolResult
from app.tools.excel.session import ExcelSession

from .vendor_models import money


class TaxPaymentImportTool:
    """Read the Saiming row and payment amount from the annual tax-payment workbook."""

    def extract(self, workbook_path: str | Path, *, expense_month: str) -> ToolResult[dict]:
        year, month = (int(part) for part in expense_month.split("-", 1))
        purpose_token = f"{year}年{month}月"
        found = None
        with ExcelSession(workbook_path, read_only=True) as session:
            for index in range(1, session.workbook.Worksheets.Count + 1):
                sheet = session.workbook.Worksheets(index)
                purpose = str(sheet.Range("C14").Text).strip()
                if purpose_token not in purpose or "个人所得税" not in purpose:
                    continue
                for row in range(1, 30):
                    company = str(sheet.Cells(row, 11).Text).strip()
                    if company == "赛倍明":
                        found = {
                            "sheet_name": str(sheet.Name),
                            "employee_count": int(sheet.Cells(row, 12).Value2 or 0),
                            "declared_tax": str(money(sheet.Cells(row, 13).Value2)),
                            "payroll_tax": str(money(sheet.Cells(row, 14).Value2)),
                            "difference": str(money(sheet.Cells(row, 15).Value2)),
                            "payment_amount": str(money(sheet.Range("C9").Value2)),
                            "purpose": purpose,
                        }
                        break
                if found:
                    break
        if not found:
            raise ExcelInputError(f"No Saiming tax payment record was found for {expense_month}")
        return ToolResult(money(found["difference"]) == money(0), "import_tax_payment", found)
