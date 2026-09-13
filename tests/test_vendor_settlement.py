from decimal import Decimal
from pathlib import Path

from app.tools.file import VendorSettlementFileTool
from app.tools.output import chinese_uppercase_currency
from app.tools.payroll import VendorEmployeeCost
from app.tools.reconciliation import VendorSettlementReconciliationTool


def record(name: str, amount: str) -> VendorEmployeeCost:
    return VendorEmployeeCost(
        vendor="测试供应商",
        expense_month="2026-08",
        employee_id=name,
        employee_name=name,
        housing_fund_company=Decimal(amount),
    )


def test_chinese_uppercase_currency() -> None:
    assert chinese_uppercase_currency("34786.14") == "叁万肆仟柒佰捌拾陆元壹角肆分"
    assert chinese_uppercase_currency("2604") == "贰仟陆佰零肆元整"


def test_reconciliation_passes_equal_records() -> None:
    result = VendorSettlementReconciliationTool().reconcile(
        [record("E001", "700")],
        [record("E001", "700")],
        fields=["housing_fund_company"],
    )
    assert result.success
    assert result.data["status"] == "passed"


def test_reconciliation_reports_difference() -> None:
    result = VendorSettlementReconciliationTool().reconcile(
        [record("E001", "700")],
        [record("E001", "600")],
        fields=["housing_fund_company"],
    )
    assert not result.success
    assert result.data["differences"][0]["difference"] == "-100.00"


def test_file_import_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"workbook-placeholder")
    destination = tmp_path / "inputs"
    tool = VendorSettlementFileTool()
    first = tool.import_file(source, destination, vendor="测试供应商", expense_month="2026-08")
    second = tool.import_file(source, destination, vendor="测试供应商", expense_month="2026-08")
    assert first.data["status"] == "accepted"
    assert second.data["status"] == "duplicate"
