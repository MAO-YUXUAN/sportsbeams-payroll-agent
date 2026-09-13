from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from app.tools.excel.errors import ExcelInputError, ExcelOperationError
from app.tools.excel.models import ToolResult
from app.tools.excel.reader import read_cell
from app.tools.excel.session import ExcelSession
from app.tools.excel.writer import save_as_xlsx, write_cell
from app.tools.payroll.vendor_models import money


CN_DIGITS = "零壹贰叁肆伍陆柒捌玖"
CN_SMALL_UNITS = ("", "拾", "佰", "仟")
CN_GROUP_UNITS = ("", "万", "亿", "兆")


def _integer_to_chinese(value: int) -> str:
    if value == 0:
        return "零"
    groups: list[int] = []
    while value:
        groups.append(value % 10000)
        value //= 10000
    parts: list[str] = []
    pending_zero = False
    for group_index in range(len(groups) - 1, -1, -1):
        group = groups[group_index]
        if group == 0:
            if parts:
                pending_zero = True
            continue
        if parts and (pending_zero or group < 1000):
            if parts[-1] != "零":
                parts.append("零")
        pending_zero = False
        group_parts: list[str] = []
        zero_inside = False
        for position in range(3, -1, -1):
            divisor = 10**position
            digit = group // divisor % 10
            if digit:
                if zero_inside and group_parts:
                    group_parts.append("零")
                group_parts.append(CN_DIGITS[digit] + CN_SMALL_UNITS[position])
                zero_inside = False
            elif group_parts and group % divisor:
                zero_inside = True
        parts.append("".join(group_parts) + CN_GROUP_UNITS[group_index])
    return "".join(parts)


def chinese_uppercase_currency(value: Decimal | str | int | float) -> str:
    amount = money(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount < 0:
        raise ExcelInputError("Payment amount cannot be negative")
    integer = int(amount)
    fraction = int((amount - Decimal(integer)) * 100)
    jiao, fen = divmod(fraction, 10)
    result = _integer_to_chinese(integer) + "元"
    if jiao == 0 and fen == 0:
        return result + "整"
    if jiao:
        result += CN_DIGITS[jiao] + "角"
    elif fen:
        result += "零"
    if fen:
        result += CN_DIGITS[fen] + "分"
    return result


class VendorPaymentRequestTool:
    """Preview or generate a vendor payment request from reconciled totals."""

    def generate(
        self,
        template_path: str | Path,
        output_path: str | Path,
        *,
        sheet_name: str,
        cell_mapping: dict[str, str | list[str]],
        values: dict[str, Any],
        reconciliation_passed: bool,
        dry_run: bool = True,
    ) -> ToolResult[dict]:
        if not reconciliation_passed:
            raise ExcelOperationError("Payment request generation requires a passed reconciliation")
        required = {"vendor_name", "payment_amount", "amount_uppercase", "purpose"}
        missing_cells = required.difference(cell_mapping)
        if missing_cells:
            raise ExcelInputError(f"Payment cell mapping is missing fields: {sorted(missing_cells)}")
        if "payment_amount" not in values:
            raise ExcelInputError("payment_amount is required")

        payment_amount = money(values["payment_amount"])
        prepared_values = dict(values)
        prepared_values["payment_amount"] = payment_amount
        prepared_values["amount_uppercase"] = chinese_uppercase_currency(payment_amount)
        changes = []
        for field, addresses in cell_mapping.items():
            if field not in prepared_values:
                continue
            for address in ([addresses] if isinstance(addresses, str) else addresses):
                changes.append({"field": field, "cell": address, "value": str(prepared_values[field])})
        result = {
            "status": "preview" if dry_run else "executing",
            "template_path": str(Path(template_path).resolve()),
            "output_path": str(Path(output_path).resolve()),
            "sheet_name": sheet_name,
            "payment_amount": str(payment_amount),
            "changes": changes,
        }
        if dry_run:
            return ToolResult(True, "generate_vendor_payment_request", result)

        saved_path, _ = save_as_xlsx(template_path, output_path)
        try:
            with ExcelSession(saved_path, read_only=False) as session:
                session.sheet(sheet_name)
                for field, addresses in cell_mapping.items():
                    if field in prepared_values:
                        for address in ([addresses] if isinstance(addresses, str) else addresses):
                            write_cell(session, sheet_name, address, prepared_values[field])
                session.workbook.Save()
            with ExcelSession(saved_path, read_only=True) as session:
                amount_addresses = cell_mapping["payment_amount"]
                amount_address = amount_addresses if isinstance(amount_addresses, str) else amount_addresses[0]
                saved_amount = money(read_cell(session, sheet_name, amount_address).value)
            if saved_amount != payment_amount:
                raise ExcelOperationError("Saved payment amount does not match the reconciled amount")
            result["status"] = "completed"
            result["output_path"] = str(saved_path)
            return ToolResult(True, "generate_vendor_payment_request", result)
        except Exception:
            saved_path.unlink(missing_ok=True)
            raise
