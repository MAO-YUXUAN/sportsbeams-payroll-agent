from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.tools.excel.models import ToolResult
from app.tools.excel.session import ExcelSession

from .vendor_models import money


FORMULA_ERRORS = ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NUM!", "#NULL!")


class FinalPayrollValidationTool:
    """Validate employee arithmetic and totals in the real Saiming monthly payroll sheet."""

    def validate(self, payroll_path: str | Path, *, expense_month: str, sheet_name: str | None = None, start_row: int = 8, end_row: int = 38, total_row: int = 38, tolerance: Decimal | str = "0.01") -> ToolResult[dict]:
        month = int(expense_month.split("-")[1])
        target_sheet = sheet_name or f"{month}月"
        tolerance_value = money(tolerance)
        arithmetic_errors, formula_errors, employees = [], [], []
        with ExcelSession(payroll_path, read_only=True) as session:
            sheet = session.sheet(target_sheet)
            for row in range(start_row, end_row + 1):
                sequence = sheet.Range(f"A{row}").Value2
                name = str(sheet.Range(f"C{row}").Text).strip()
                if not isinstance(sequence, (int, float)) or not name:
                    continue
                gross = money(sheet.Range(f"P{row}").Value2)
                personal = money(sheet.Range(f"AE{row}").Value2)
                tax = money(sheet.Range(f"AI{row}").Value2)
                net = money(sheet.Range(f"AJ{row}").Value2)
                expected_gross = money(sum((money(sheet.Cells(row, col).Value2) for col in range(8, 13)), Decimal("0")) - sum((money(sheet.Cells(row, col).Value2) for col in range(13, 16)), Decimal("0")))
                expected_net = money(gross - personal - tax)
                if abs(gross - expected_gross) > tolerance_value:
                    arithmetic_errors.append({"employee_name": name, "field": "gross_pay", "actual": str(gross), "expected": str(expected_gross)})
                if abs(net - expected_net) > tolerance_value:
                    arithmetic_errors.append({"employee_name": name, "field": "net_pay", "actual": str(net), "expected": str(expected_net)})
                employees.append({"employee_name": name, "gross_pay": str(gross), "personal_deductions": str(personal), "income_tax": str(tax), "net_pay": str(net)})
            used = sheet.UsedRange
            for row in range(1, used.Rows.Count + 1):
                for column in range(1, used.Columns.Count + 1):
                    cell = used.Cells(row, column)
                    text = str(cell.Text)
                    if text.startswith(FORMULA_ERRORS):
                        formula_errors.append({"address": str(cell.Address).replace("$", ""), "value": text})
            total_checks = {}
            for field, column in {"gross_pay": "P", "personal_deductions": "AE", "income_tax": "AI", "net_pay": "AJ"}.items():
                employee_sum = sum((money(item[field]) for item in employees), Decimal("0"))
                reported = money(sheet.Range(f"{column}{total_row}").Value2)
                total_checks[field] = {"employee_sum": str(employee_sum), "reported_total": str(reported), "difference": str(money(reported - employee_sum))}
        total_errors = {field: value for field, value in total_checks.items() if abs(money(value["difference"])) > tolerance_value}
        passed = not arithmetic_errors and not formula_errors and not total_errors
        return ToolResult(passed, "validate_final_payroll", {"status": "passed" if passed else "blocked", "sheet_name": target_sheet, "employee_count": len(employees), "arithmetic_errors": arithmetic_errors, "formula_errors": formula_errors, "total_checks": total_checks, "total_errors": total_errors})
