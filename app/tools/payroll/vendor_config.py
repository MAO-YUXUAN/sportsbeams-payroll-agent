from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from app.tools.excel.errors import ExcelInputError

from .vendor_models import SettlementMapping, SummaryMapping


@dataclass(slots=True)
class VendorWorkflowConfig:
    id: str
    display_name: str
    vendor_name: str
    file_patterns: list[str]
    settlement: SettlementMapping
    payroll_summary: SummaryMapping
    reconciliation_fields: list[str]
    payment_sheet_name: str
    payment_cells: dict[str, str | list[str]]
    payment_purpose: str


def load_vendor_config(config_path: str | Path, expense_month: str) -> VendorWorkflowConfig:
    """Load a supplier YAML and resolve its month placeholders (YYYY-MM)."""
    try:
        year_text, month_text = expense_month.split("-", 1)
        month_date = date(int(year_text), int(month_text), 1)
    except (TypeError, ValueError) as error:
        raise ExcelInputError("expense_month must use YYYY-MM format") from error

    path = Path(config_path).resolve()
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    context = {
        "expense_month": expense_month,
        "expense_yyyymm": month_date.strftime("%Y%m"),
        "year": month_date.year,
        "month": month_date.month,
    }
    settlement_raw = raw["settlement"]
    summary_raw = raw["payroll_summary"]
    payment_raw = raw["payment_request"]

    settlement = SettlementMapping(
        sheet_name=str(settlement_raw["sheet_name"]).format(**context),
        start_row=int(settlement_raw["start_row"]),
        columns=dict(settlement_raw["columns"]),
        sum_columns={key: list(value) for key, value in settlement_raw.get("sum_columns", {}).items()},
        end_row=settlement_raw.get("end_row"),
        blank_row_limit=int(settlement_raw.get("blank_row_limit", 3)),
        excluded_names=list(settlement_raw.get("excluded_names", ["合计", "总计", "小计"])),
        total_cell=settlement_raw.get("total_cell"),
    )
    summary = SummaryMapping(
        sheet_name=summary_raw["sheet_name"],
        start_row=int(summary_raw["start_row"]),
        end_row=int(summary_raw["end_row"]),
        employee_id_column=summary_raw.get("employee_id_column"),
        employee_name_column=summary_raw["employee_name_column"],
        field_columns=dict(summary_raw["field_columns"]),
        expense_month_column=summary_raw.get("expense_month_column"),
        match_by=summary_raw.get("match_by", "employee_id"),
    )
    if summary.match_by not in {"employee_id", "employee_name"}:
        raise ExcelInputError("payroll_summary.match_by must be employee_id or employee_name")

    return VendorWorkflowConfig(
        id=raw["id"],
        display_name=raw["display_name"],
        vendor_name=raw["vendor_name"],
        file_patterns=list(raw.get("file_patterns", [])),
        settlement=settlement,
        payroll_summary=summary,
        reconciliation_fields=list(raw["reconciliation_fields"]),
        payment_sheet_name=payment_raw["sheet_name"],
        payment_cells=dict(payment_raw["cells"]),
        payment_purpose=str(payment_raw["purpose_template"]).format(**context),
    )
