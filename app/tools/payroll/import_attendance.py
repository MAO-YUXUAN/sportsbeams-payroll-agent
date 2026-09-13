from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from app.tools.excel.errors import ExcelInputError
from app.tools.excel.models import ToolResult
from app.tools.excel.session import ExcelSession

from .records import AttendanceRecord


class AttendanceImportTool:
    """Read the real attendance matrix (dates in rows, employees in columns)."""

    def extract(
        self,
        workbook_path: str | Path,
        *,
        expense_month: str,
        sheet_name: str = "Sheet2",
        employee_header_row: int = 4,
        date_column: str = "B",
        first_employee_column: int = 4,
    ) -> ToolResult[dict]:
        records = []
        with ExcelSession(workbook_path, read_only=True) as session:
            sheet = session.sheet(sheet_name)
            last_row = int(sheet.UsedRange.Row + sheet.UsedRange.Rows.Count - 1)
            last_column = int(sheet.UsedRange.Column + sheet.UsedRange.Columns.Count - 1)
            scheduled_workdays = 0
            for row in range(employee_header_row + 1, last_row + 1):
                value = sheet.Range(f"{date_column}{row}").Value2
                if self._is_weekday(value):
                    scheduled_workdays += 1
            for column in range(first_employee_column, last_column + 1):
                employee_name = str(sheet.Cells(employee_header_row, column).Text).strip()
                if not employee_name:
                    continue
                dated_statuses = []
                counts: Counter[str] = Counter()
                for row in range(employee_header_row + 1, last_row + 1):
                    date_text = str(sheet.Range(f"{date_column}{row}").Text).strip()
                    status = str(sheet.Cells(row, column).Text).strip()
                    if date_text and status:
                        dated_statuses.append({"date": date_text, "status": status})
                        counts[status] += 1
                records.append(
                    AttendanceRecord(
                        employee_name=employee_name,
                        expense_month=expense_month,
                        status_counts=dict(counts),
                        dated_statuses=dated_statuses,
                        source_file=str(Path(workbook_path).resolve()),
                        source_sheet=sheet_name,
                        scheduled_workdays=scheduled_workdays,
                    )
                )
        if not records:
            raise ExcelInputError("No employees were found in the attendance workbook")
        return ToolResult(
            True,
            "import_attendance",
            {"expense_month": expense_month, "record_count": len(records), "records": [r.to_dict() for r in records], "record_objects": records},
        )

    @staticmethod
    def _is_weekday(value: object) -> bool:
        if value in (None, ""):
            return False
        if isinstance(value, (int, float)):
            moment = datetime(1899, 12, 30) + timedelta(days=float(value))
            return moment.weekday() < 5
        text = str(value).strip()
        for pattern in ("%Y/%m/%d", "%Y-%m-%d", "%Y年%m月%d日", "%m/%d/%Y"):
            try:
                return datetime.strptime(text, pattern).weekday() < 5
            except ValueError:
                continue
        return False
