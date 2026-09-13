from decimal import Decimal

from app.tools.excel.models import ToolResult
from app.tools.output import ReconciliationReportTool
from app.tools.payroll import EmployeeAmountRecord
from app.tools.reconciliation import EmployeeAmountReconciliationTool


def _record(name: str, gross: str) -> EmployeeAmountRecord:
    return EmployeeAmountRecord(name, "2026-07", {"gross_pay": Decimal(gross)})


def test_employee_reconciliation_passes_equal_records():
    result = EmployeeAmountReconciliationTool().reconcile(
        [_record("张三", "100.00")], [_record("张三", "100.00")], fields=["gross_pay"]
    )
    assert result.success


def test_employee_reconciliation_normalizes_month_format():
    expected = _record("张三", "100.00")
    actual = EmployeeAmountRecord("张三", "202607", {"gross_pay": Decimal("100.00")})
    assert EmployeeAmountReconciliationTool().reconcile([expected], [actual], fields=["gross_pay"]).success


def test_reconciliation_report_records_blocked_status(tmp_path):
    result = ReconciliationReportTool().generate(
        tmp_path / "report.json",
        run_id="run-001",
        expense_month="2026-07",
        results={"check": ToolResult(False, "check", {"difference": "1.00"})},
    )
    assert not result.success
    assert result.data["status"] == "blocked"
