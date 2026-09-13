from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from app.tools.excel.errors import ExcelInputError


MONEY = Decimal("0.01")
AMOUNT_FIELDS = (
    "pension_company",
    "pension_employee",
    "medical_company",
    "medical_employee",
    "unemployment_company",
    "unemployment_employee",
    "housing_fund_company",
    "housing_fund_employee",
    "company_subtotal",
    "employee_subtotal",
    "service_fee",
    "row_total",
)
COMPONENT_FIELDS = AMOUNT_FIELDS[:8]


def money(value: Any, *, blank_as_zero: bool = True) -> Decimal:
    if value in (None, ""):
        if blank_as_zero:
            return Decimal("0.00")
        raise ExcelInputError("Required amount is blank")
    try:
        normalized = str(value).replace(",", "").replace("¥", "").strip()
        return Decimal(normalized).quantize(MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as error:
        raise ExcelInputError(f"Invalid monetary value: {value!r}") from error


@dataclass(slots=True)
class VendorEmployeeCost:
    vendor: str
    expense_month: str
    employee_id: str
    employee_name: str
    pension_company: Decimal = Decimal("0.00")
    pension_employee: Decimal = Decimal("0.00")
    medical_company: Decimal = Decimal("0.00")
    medical_employee: Decimal = Decimal("0.00")
    unemployment_company: Decimal = Decimal("0.00")
    unemployment_employee: Decimal = Decimal("0.00")
    housing_fund_company: Decimal = Decimal("0.00")
    housing_fund_employee: Decimal = Decimal("0.00")
    company_subtotal: Decimal = Decimal("0.00")
    employee_subtotal: Decimal = Decimal("0.00")
    service_fee: Decimal = Decimal("0.00")
    row_total: Decimal = Decimal("0.00")
    source_file: str = ""
    source_sheet: str = ""
    source_row: int = 0

    @property
    def match_key(self) -> str:
        identifier = self.employee_id.strip()
        base = f"id:{identifier}" if identifier else f"name:{self.employee_name.strip()}"
        return f"{base}|month:{self.expense_month.strip()}"

    @property
    def calculated_total(self) -> Decimal:
        if self.company_subtotal or self.employee_subtotal:
            return (self.company_subtotal + self.employee_subtotal + self.service_fee).quantize(MONEY)
        return (
            sum((getattr(self, field) for field in COMPONENT_FIELDS), Decimal("0.00")) + self.service_fee
        ).quantize(MONEY)

    def amount(self, field_name: str) -> Decimal:
        if field_name not in AMOUNT_FIELDS:
            raise ExcelInputError(f"Unsupported amount field: {field_name}")
        return getattr(self, field_name)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for field_name in AMOUNT_FIELDS:
            result[field_name] = str(result[field_name])
        result["calculated_total"] = str(self.calculated_total)
        result["match_key"] = self.match_key
        return result


@dataclass(slots=True)
class SettlementMapping:
    sheet_name: str
    start_row: int
    columns: dict[str, str]
    sum_columns: dict[str, list[str]] = field(default_factory=dict)
    end_row: int | None = None
    blank_row_limit: int = 3
    excluded_names: list[str] = field(default_factory=lambda: ["合计", "总计", "小计"])
    total_cell: str | None = None


@dataclass(slots=True)
class SummaryMapping:
    sheet_name: str
    start_row: int
    end_row: int
    employee_id_column: str | None
    employee_name_column: str
    field_columns: dict[str, str]
    expense_month_column: str | None = None
    match_by: str = "employee_id"
