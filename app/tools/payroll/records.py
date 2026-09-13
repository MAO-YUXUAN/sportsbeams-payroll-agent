from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from .vendor_models import money


@dataclass(slots=True)
class EmployeeAmountRecord:
    employee_name: str
    expense_month: str
    values: dict[str, Decimal] = field(default_factory=dict)
    employee_id: str = ""
    department: str = ""
    source_file: str = ""
    source_sheet: str = ""
    source_row: int = 0

    @property
    def match_key(self) -> str:
        normalized_month = self.expense_month.strip().replace("-", "")
        return f"name:{self.employee_name.strip()}|month:{normalized_month}"

    def amount(self, field_name: str) -> Decimal:
        return money(self.values.get(field_name, 0))

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["values"] = {key: str(value) for key, value in self.values.items()}
        result["match_key"] = self.match_key
        return result


@dataclass(slots=True)
class AttendanceRecord:
    employee_name: str
    expense_month: str
    status_counts: dict[str, int]
    dated_statuses: list[dict[str, str]]
    source_file: str
    source_sheet: str
    scheduled_workdays: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
