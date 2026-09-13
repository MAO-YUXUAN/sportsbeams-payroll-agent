from __future__ import annotations

from decimal import Decimal

from app.tools.excel.errors import ExcelInputError
from app.tools.excel.models import ToolResult
from app.tools.payroll.vendor_models import AMOUNT_FIELDS, MONEY, VendorEmployeeCost, money


class VendorSettlementReconciliationTool:
    """Reconcile vendor employee costs against payroll employee costs."""

    def reconcile(
        self,
        settlement_records: list[VendorEmployeeCost],
        payroll_records: list[VendorEmployeeCost],
        *,
        fields: list[str] | None = None,
        tolerance: Decimal | str = Decimal("0.01"),
    ) -> ToolResult[dict]:
        compare_fields = fields or list(AMOUNT_FIELDS)
        invalid = [field for field in compare_fields if field not in AMOUNT_FIELDS]
        if invalid:
            raise ExcelInputError(f"Unsupported reconciliation fields: {invalid}")
        tolerance_value = money(tolerance)
        settlement_index = self._unique_index(settlement_records, "settlement")
        payroll_index = self._unique_index(payroll_records, "payroll")

        missing_in_payroll = sorted(set(settlement_index).difference(payroll_index))
        extra_in_payroll = sorted(set(payroll_index).difference(settlement_index))
        differences: list[dict] = []
        for key in sorted(set(settlement_index).intersection(payroll_index)):
            settlement = settlement_index[key]
            payroll = payroll_index[key]
            for field_name in compare_fields:
                expected = settlement.amount(field_name)
                actual = payroll.amount(field_name)
                difference = (actual - expected).quantize(MONEY)
                if abs(difference) > tolerance_value:
                    differences.append(
                        {
                            "match_key": key,
                            "employee_name": settlement.employee_name,
                            "field": field_name,
                            "settlement_amount": str(expected),
                            "payroll_amount": str(actual),
                            "difference": str(difference),
                            "severity": "BLOCKER",
                        }
                    )

        settlement_total = sum(
            (record.amount(field) for record in settlement_records for field in compare_fields), Decimal("0.00")
        )
        payroll_total = sum(
            (record.amount(field) for record in payroll_records for field in compare_fields), Decimal("0.00")
        )
        settlement_total = settlement_total.quantize(MONEY)
        payroll_total = payroll_total.quantize(MONEY)
        total_difference = (payroll_total - settlement_total).quantize(MONEY)
        passed = not missing_in_payroll and not extra_in_payroll and not differences and abs(total_difference) <= tolerance_value
        return ToolResult(
            passed,
            "reconcile_vendor_settlement",
            {
                "status": "passed" if passed else "blocked",
                "fields": compare_fields,
                "tolerance": str(tolerance_value),
                "missing_in_payroll": missing_in_payroll,
                "extra_in_payroll": extra_in_payroll,
                "differences": differences,
                "settlement_total": str(settlement_total),
                "payroll_total": str(payroll_total),
                "total_difference": str(total_difference),
            },
        )

    @staticmethod
    def _unique_index(records: list[VendorEmployeeCost], label: str) -> dict[str, VendorEmployeeCost]:
        result: dict[str, VendorEmployeeCost] = {}
        duplicates: list[str] = []
        for record in records:
            if record.match_key in result:
                duplicates.append(record.match_key)
            result[record.match_key] = record
        if duplicates:
            raise ExcelInputError(f"Duplicate {label} employee keys: {sorted(set(duplicates))}")
        return result
