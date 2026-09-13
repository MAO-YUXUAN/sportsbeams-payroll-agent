from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from app.tools.excel.errors import ExcelOperationError
from app.tools.excel.models import ToolResult
from app.tools.reconciliation.vendor_settlement import VendorSettlementReconciliationTool

from .import_vendor_costs import VendorCostImportTool
from .update_department_summary import DepartmentSummaryTool
from .vendor_models import SettlementMapping, SummaryMapping


class ProcessVendorSettlementTool:
    """Compose extraction, payroll update, reconciliation, and payment generation."""

    def process(
        self,
        *,
        settlement_path: str | Path,
        payroll_path: str | Path,
        payroll_output_path: str | Path,
        payment_template_path: str | Path,
        payment_output_path: str | Path,
        vendor: str,
        expense_month: str,
        settlement_mapping: SettlementMapping,
        summary_mapping: SummaryMapping,
        payment_sheet: str,
        payment_cell_mapping: dict[str, str],
        payment_values: dict[str, Any],
        reconciliation_fields: list[str],
        tolerance: Decimal | str = Decimal("0.01"),
        dry_run: bool = True,
    ) -> ToolResult[dict]:
        from app.tools.output.vendor_payment_request import VendorPaymentRequestTool

        extraction = VendorCostImportTool().extract(
            settlement_path,
            vendor=vendor,
            expense_month=expense_month,
            mapping=settlement_mapping,
        )
        records = extraction.data["record_objects"]
        summary_tool = DepartmentSummaryTool()
        update_preview = summary_tool.update(payroll_path, records, summary_mapping, dry_run=True)
        blocked = not update_preview.success

        payment_values = dict(payment_values)
        payment_values.setdefault("vendor_name", vendor)
        payment_values.setdefault("expense_month", expense_month)
        proposed_payment_total = sum(
            (record.amount(field) for record in records for field in reconciliation_fields),
            Decimal("0.00"),
        )
        payment_values.setdefault("payment_amount", str(proposed_payment_total))

        result = {
            "status": "blocked" if blocked else "preview",
            "extraction": {key: value for key, value in extraction.data.items() if key != "record_objects"},
            "payroll_update": update_preview.data,
            "reconciliation": None,
            "payment_request": None,
        }
        if blocked:
            return ToolResult(False, "process_vendor_settlement", result, extraction.warnings)

        if dry_run:
            payment_preview = VendorPaymentRequestTool().generate(
                payment_template_path,
                payment_output_path,
                sheet_name=payment_sheet,
                cell_mapping=payment_cell_mapping,
                values=payment_values,
                reconciliation_passed=True,
                dry_run=True,
            )
            result["payment_request"] = payment_preview.data
            return ToolResult(True, "process_vendor_settlement", result, extraction.warnings)

        payroll_update = summary_tool.update(
            payroll_path,
            records,
            summary_mapping,
            output_path=payroll_output_path,
            dry_run=False,
        )
        payroll_records = summary_tool.read_summary(payroll_output_path, records, summary_mapping)
        reconciliation = VendorSettlementReconciliationTool().reconcile(
            records,
            payroll_records,
            fields=reconciliation_fields,
            tolerance=tolerance,
        )
        if not reconciliation.success:
            raise ExcelOperationError("Updated payroll workbook did not pass vendor reconciliation")
        payment_values["payment_amount"] = reconciliation.data["settlement_total"]
        payment = VendorPaymentRequestTool().generate(
            payment_template_path,
            payment_output_path,
            sheet_name=payment_sheet,
            cell_mapping=payment_cell_mapping,
            values=payment_values,
            reconciliation_passed=True,
            dry_run=False,
        )
        result.update(
            {
                "status": "completed",
                "payroll_update": payroll_update.data,
                "reconciliation": reconciliation.data,
                "payment_request": payment.data,
            }
        )
        return ToolResult(True, "process_vendor_settlement", result, extraction.warnings)
