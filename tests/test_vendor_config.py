from pathlib import Path

from app.tools.payroll.vendor_config import load_vendor_config


CONFIG_DIR = Path(__file__).parents[1] / "knowledge" / "field-mappings" / "vendors"


def test_yarun_config_resolves_month_sheet_and_real_target_block():
    config = load_vendor_config(CONFIG_DIR / "yarun.yaml", "2026-07")

    assert config.settlement.sheet_name == "202607"
    assert config.settlement.columns["company_subtotal"] == "P"
    assert config.payroll_summary.start_row == 19
    assert config.payroll_summary.expense_month_column == "H"
    assert config.payment_cells["payment_amount"] == ["C8", "D14"]


def test_yicai_config_combines_fee_and_matches_name_plus_month():
    config = load_vendor_config(CONFIG_DIR / "yicai.yaml", "2026-07")

    assert config.settlement.sum_columns["service_fee"] == ["I", "J"]
    assert config.settlement.sum_columns["row_total"] == ["CK", "CL", "I", "J"]
    assert config.payroll_summary.match_by == "employee_name"
    assert config.payroll_summary.expense_month_column == "H"
    assert config.reconciliation_fields == [
        "company_subtotal",
        "employee_subtotal",
        "service_fee",
    ]
