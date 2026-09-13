from pathlib import Path

import pytest

from app.tools.excel.errors import ExcelInputError
from app.tools.payroll.rollover_month import PayrollRolloverTool


def test_formula_replacement_requires_all_fields() -> None:
    with pytest.raises(ExcelInputError):
        PayrollRolloverTool._validate_replacement({"old": "6月", "new": "7月"})


def test_formula_replacement_rejects_equal_values() -> None:
    with pytest.raises(ExcelInputError):
        PayrollRolloverTool._validate_replacement(
            {"old": "6月", "new": "6月", "reason": "invalid"}
        )


def test_rollover_rejects_invalid_copy_position_before_opening_excel(tmp_path: Path) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"not opened")
    with pytest.raises(ExcelInputError):
        PayrollRolloverTool().rollover_payroll_month(
            source,
            tmp_path / "output.xlsx",
            "7月",
            "8月",
            "A1",
            "2026年8月工资",
            copy_position="middle",
        )


def test_rollover_defaults_to_copying_new_month_after_source_sheet() -> None:
    defaults = PayrollRolloverTool.rollover_payroll_month.__kwdefaults__
    assert defaults is not None
    assert defaults["copy_position"] == "after"
