from __future__ import annotations

import calendar
from datetime import datetime
from pathlib import Path
from typing import Any

from app.tools.excel.errors import ExcelInputError, ExcelOperationError
from app.tools.excel.models import ToolResult

from .records import AttendanceRecord


XL_OPEN_XML_WORKBOOK = 51


class MealAllowanceGenerateTool:
    """Generate the monthly meal workbook from attendance and configured employees."""

    def generate(
        self,
        attendance_records: list[AttendanceRecord],
        output_path: str | Path,
        *,
        expense_month: str,
        employees: list[dict[str, str]],
        daily_rate: int = 15,
        deductible_statuses: list[str] | None = None,
    ) -> ToolResult[dict[str, Any]]:
        if not employees:
            raise ExcelInputError("Meal-allowance employee configuration is empty")
        output = Path(output_path).expanduser().resolve()
        if output.suffix.casefold() != ".xlsx":
            raise ExcelInputError("Meal-allowance output must be an .xlsx workbook")
        if output.exists():
            raise ExcelInputError(f"Meal-allowance output already exists: {output}")
        year, month = (int(part) for part in expense_month.split("-"))
        attendance_workdays = {
            record.scheduled_workdays for record in attendance_records
            if record.scheduled_workdays > 0
        }
        if len(attendance_workdays) > 1:
            raise ExcelOperationError("Attendance records use inconsistent scheduled workdays")
        working_days = next(iter(attendance_workdays), 0)
        if not working_days:
            working_days = sum(
                1 for day in range(1, calendar.monthrange(year, month)[1] + 1)
                if datetime(year, month, day).weekday() < 5
            )
        deductible = set(deductible_statuses or ["出差", "年假", "事假", "病假", "旷工"])
        attendance = {record.employee_name.strip(): record for record in attendance_records}
        rows: list[dict[str, Any]] = []
        for sequence, employee in enumerate(employees, start=1):
            name = employee["employee_name"].strip()
            record = attendance.get(name)
            if record is None:
                raise ExcelOperationError(f"Meal employee was not found in attendance: {name}")
            deduction_days = sum(
                1 for item in record.dated_statuses
                if item["status"].strip() in deductible and self._is_weekday(item["date"], year)
            )
            rows.append({
                "sequence": sequence,
                "department": employee.get("department", ""),
                "employee_name": name,
                "working_days": working_days,
                "deduction_days": deduction_days,
            })
        output.parent.mkdir(parents=True, exist_ok=True)
        self._write_workbook(output, year, month, daily_rate, rows)
        total = sum((working_days - row["deduction_days"]) * daily_rate for row in rows)
        return ToolResult(True, "generate_meal_allowance", {
            "path": str(output), "expense_month": expense_month, "record_count": len(rows),
            "working_days": working_days, "daily_rate": daily_rate, "total": str(total), "rows": rows,
            "requires_confirmation": True,
            "confirmation_note": "出差/请假天数需按流程由舒凯彪确认；考勤表未包含确认后的调整值。",
        })

    @staticmethod
    def _is_weekday(value: str, default_year: int) -> bool:
        text = value.strip()
        for pattern in ("%Y/%m/%d", "%Y-%m-%d", "%m/%d/%Y", "%Y年%m月%d日"):
            try:
                return datetime.strptime(text, pattern).weekday() < 5
            except ValueError:
                pass
        try:
            return datetime.strptime(f"{default_year}年{text}", "%Y年%m月%d日").weekday() < 5
        except ValueError:
            pass
        raise ExcelInputError(f"Unsupported attendance date: {value}")

    @staticmethod
    def _write_workbook(output: Path, year: int, month: int, daily_rate: int, rows: list[dict[str, Any]]) -> None:
        try:
            import pythoncom
            import win32com.client
        except ImportError as error:
            raise ExcelOperationError("pywin32 is required to generate the meal workbook") from error
        pythoncom.CoInitialize()
        app = workbook = None
        try:
            app = win32com.client.DispatchEx("Excel.Application")
            app.Visible = False; app.DisplayAlerts = False; app.ScreenUpdating = False; app.EnableEvents = False
            workbook = app.Workbooks.Add()
            sheet = workbook.Worksheets(1); sheet.Name = f"{month}月"
            sheet.Range("A1:I1").Merge()
            sheet.Range("A1").Value2 = f"赛倍明{year}年{month}月份餐费"
            sheet.Range("A2:I2").Value2 = (("序号", "部门", "员工姓名", f"{month}月出勤{rows[0]['working_days']}天", "请假/出差", "加班补贴", "请客补贴", "总金额", "签字"),)
            for index, row in enumerate(rows, start=3):
                sheet.Range(f"A{index}:C{index}").Value2 = ((row["sequence"], row["department"], row["employee_name"]),)
                sheet.Range(f"D{index}").Formula = f"={daily_rate}*{row['working_days']}"
                sheet.Range(f"E{index}").Formula = f"={daily_rate}*{row['deduction_days']}"
                sheet.Range(f"H{index}").Formula = f"=D{index}-E{index}+F{index}+G{index}"
            total_row = len(rows) + 3
            sheet.Range(f"A{total_row}:C{total_row}").Merge(); sheet.Range(f"A{total_row}").Value2 = "合计"
            for column in "DEFG": sheet.Range(f"{column}{total_row}").Formula = f"=SUM({column}3:{column}{total_row - 1})"
            sheet.Range(f"H{total_row}").Formula = f"=D{total_row}-E{total_row}+F{total_row}+G{total_row}"
            used = sheet.Range(f"A1:I{total_row}")
            used.Font.Name = "微软雅黑"; used.Font.Size = 10; used.VerticalAlignment = -4108
            sheet.Range("A1:I1").Font.Size = 16; sheet.Range("A1:I1").Font.Bold = True; sheet.Range("A1:I1").HorizontalAlignment = -4108
            sheet.Range("A2:I2").Font.Bold = True; sheet.Range("A2:I2").HorizontalAlignment = -4108
            sheet.Range(f"A2:I{total_row}").Borders.LineStyle = 1
            sheet.Range(f"A3:A{total_row - 1}").HorizontalAlignment = -4108
            sheet.Range(f"C3:C{total_row - 1}").HorizontalAlignment = -4108
            sheet.Columns("A").ColumnWidth = 7; sheet.Columns("B").ColumnWidth = 14; sheet.Columns("C").ColumnWidth = 12
            sheet.Columns("D:H").ColumnWidth = 13; sheet.Columns("I").ColumnWidth = 12
            sheet.Rows(1).RowHeight = 28; sheet.Rows(2).RowHeight = 24
            sheet.PageSetup.Orientation = 2; sheet.PageSetup.FitToPagesWide = 1; sheet.PageSetup.FitToPagesTall = False
            workbook.SaveAs(str(output), FileFormat=XL_OPEN_XML_WORKBOOK)
        finally:
            if workbook is not None:
                try: workbook.Close(SaveChanges=False)
                except Exception: pass
            if app is not None:
                try: app.Quit()
                except Exception: pass
            pythoncom.CoUninitialize()
