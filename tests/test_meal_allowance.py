from app.tools.payroll import AttendanceImportTool, MealAllowanceGenerateTool


def test_meal_allowance_date_without_year_uses_expense_year():
    tool = MealAllowanceGenerateTool()
    assert tool._is_weekday("6月26日", 2026) is True
    assert tool._is_weekday("6月28日", 2026) is False


def test_meal_allowance_accepts_full_date_formats():
    tool = MealAllowanceGenerateTool()
    assert tool._is_weekday("2026/7/24", 2026) is True
    assert tool._is_weekday("2026-07-25", 2026) is False


def test_attendance_excel_serial_counts_only_weekdays():
    assert AttendanceImportTool._is_weekday(46199) is True
    assert AttendanceImportTool._is_weekday(46200) is False
    assert AttendanceImportTool._is_weekday(46201) is False
