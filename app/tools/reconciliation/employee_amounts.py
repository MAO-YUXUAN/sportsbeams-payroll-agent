from __future__ import annotations

from decimal import Decimal

from app.tools.excel.errors import ExcelInputError
from app.tools.excel.models import ToolResult
from app.tools.payroll.records import EmployeeAmountRecord
from app.tools.payroll.vendor_models import money


class EmployeeAmountReconciliationTool:
    """Reconcile any two employee-level datasets by name + expense month."""

    def reconcile(self, expected: list[EmployeeAmountRecord], actual: list[EmployeeAmountRecord], *, fields: list[str], tolerance: Decimal | str = "0.01") -> ToolResult[dict]:
        if not fields:
            raise ExcelInputError("At least one reconciliation field is required")
        expected_index = self._index(expected, "expected")
        actual_index = self._index(actual, "actual")
        tolerance_value = money(tolerance)
        missing = sorted(set(expected_index) - set(actual_index))
        extra = sorted(set(actual_index) - set(expected_index))
        differences = []
        for key in sorted(set(expected_index) & set(actual_index)):
            for field in fields:
                left = expected_index[key].amount(field)
                right = actual_index[key].amount(field)
                delta = money(right - left)
                if abs(delta) > tolerance_value:
                    differences.append({"match_key": key, "employee_name": expected_index[key].employee_name, "field": field, "expected": str(left), "actual": str(right), "difference": str(delta)})
        passed = not missing and not extra and not differences
        return ToolResult(passed, "reconcile_employee_amounts", {"status": "passed" if passed else "blocked", "fields": fields, "missing_in_actual": missing, "extra_in_actual": extra, "differences": differences})

    @staticmethod
    def _index(records: list[EmployeeAmountRecord], label: str) -> dict[str, EmployeeAmountRecord]:
        result = {}
        for record in records:
            if record.match_key in result:
                raise ExcelInputError(f"Duplicate {label} record: {record.match_key}")
            result[record.match_key] = record
        return result
