from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import re
import shutil

from app.tools.excel.errors import ExcelInputError, ExcelOperationError
from app.tools.excel.models import ToolResult
from app.tools.excel.session import ExcelSession


class NewEmployeePayrollTool:
    """Insert a confirmed employee into a department block without guessing pay data."""

    def add(
        self,
        payroll_path: str | Path,
        *,
        sheet_name: str,
        employee_name: str,
        department: str,
        base_salary: Decimal,
        backup_path: str | Path,
    ) -> ToolResult[dict]:
        source = Path(payroll_path).expanduser().resolve()
        backup = Path(backup_path).expanduser().resolve()
        if not source.is_file():
            raise ExcelInputError(f"Payroll workbook does not exist: {source}")
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            shutil.copy2(source, backup)

        with ExcelSession(source, read_only=False) as session:
            sheet = session.sheet(sheet_name)
            used_last_row = int(sheet.UsedRange.Row + sheet.UsedRange.Rows.Count - 1)
            for row in range(8, used_last_row + 1):
                if str(sheet.Range(f"C{row}").Text).strip() == employee_name:
                    return ToolResult(True, "add_new_employee", {
                        "status": "already_exists", "employee_name": employee_name,
                        "department": department, "row": row, "output_path": str(source),
                        "backup_path": str(backup),
                    })

            department_row = next(
                (row for row in range(8, used_last_row + 1)
                 if str(sheet.Range(f"B{row}").Text).strip() == department),
                None,
            )
            if department_row is None:
                raise ExcelInputError(f"Department does not exist in payroll sheet: {department}")
            subtotal_row = next(
                (row for row in range(department_row + 1, used_last_row + 1)
                 if "小计" in str(sheet.Range(f"A{row}").Text)),
                None,
            )
            if subtotal_row is None or subtotal_row <= department_row + 0:
                raise ExcelOperationError(f"Could not locate subtotal row for department: {department}")

            insert_row = subtotal_row
            sheet.Rows(insert_row).Insert()
            sheet.Rows(insert_row - 1).Copy(sheet.Rows(insert_row))
            sheet.Range(f"A{insert_row}").Value2 = int(sheet.Range(f"A{insert_row - 1}").Value2 or 0) + 1
            sheet.Range(f"B{insert_row}").ClearContents()
            sheet.Range(f"C{insert_row}").Value2 = employee_name

            # Preserve copied formulas and formatting, but never inherit another
            # employee's identifiers or manually entered payroll amounts.
            generic_formula_columns = {
                8, 16, 18, 19, 20, 21, 22, 23, 24, 25, 26,
                27, 28, 29, 30, 31, 33, 34, 35, 36, 38,
            }
            for column in range(4, 49):
                cell = sheet.Cells(insert_row, column)
                if not bool(cell.HasFormula) or column not in generic_formula_columns:
                    cell.ClearContents()
            sheet.Range(f"E{insert_row}").Value2 = float(base_salary)

            # Excel does not consistently expand a subtotal when a row is
            # inserted immediately above it and then populated by Copy. Extend
            # the ending row reference explicitly for every subtotal formula.
            subtotal_row_after_insert = insert_row + 1
            for column in range(5, 49):
                cell = sheet.Cells(subtotal_row_after_insert, column)
                if not bool(cell.HasFormula):
                    continue
                formula = str(cell.Formula)
                formula = re.sub(
                    rf":(\$?[A-Z]{{1,3}}\$?){insert_row - 1}(?!\d)",
                    rf":\g<1>{insert_row}",
                    formula,
                )
                cell.Formula = formula

            sequence = 0
            used_last_row = int(sheet.UsedRange.Row + sheet.UsedRange.Rows.Count - 1)
            for row in range(8, used_last_row + 1):
                if str(sheet.Range(f"C{row}").Text).strip():
                    sequence += 1
                    sheet.Range(f"A{row}").Value2 = sequence

            session.app.CalculateFullRebuild()

            if str(sheet.Range(f"C{insert_row}").Text).strip() != employee_name:
                raise ExcelOperationError("New employee name was not persisted")
            if Decimal(str(sheet.Range(f"E{insert_row}").Value2 or 0)) != base_salary:
                raise ExcelOperationError("New employee base salary was not persisted")
            subtotal_formula = str(sheet.Range(f"E{insert_row + 1}").Formula)
            if f"E{insert_row}" not in subtotal_formula:
                raise ExcelOperationError("Department subtotal did not expand to include the new employee")
            session.workbook.Save()

        return ToolResult(True, "add_new_employee", {
            "status": "completed", "employee_name": employee_name,
            "department": department, "row": insert_row,
            "base_salary": str(base_salary), "output_path": str(source),
            "backup_path": str(backup),
        })
