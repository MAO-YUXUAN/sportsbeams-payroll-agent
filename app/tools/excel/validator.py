from __future__ import annotations

from .models import CellData, WorkbookValidation, json_safe
from .reader import list_sheets
from .session import ExcelSession


FORMULA_ERROR_PREFIXES = ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NUM!", "#NULL!")
XL_CELL_TYPE_FORMULAS = -4123
XL_ERRORS = 16


def validate_workbook(session: ExcelSession, required_sheets: list[str] | None = None) -> WorkbookValidation:
    required = required_sheets or []
    present = set(list_sheets(session))
    missing = [name for name in required if name not in present]
    formula_errors: list[CellData] = []

    sheets_to_scan = [name for name in required if name in present] if required else list(present)
    for sheet_name in sheets_to_scan:
        sheet = session.sheet(sheet_name)
        # Let Excel locate formula errors natively. Reading Cell.Text through COM for
        # every cell in a formatted payroll sheet can otherwise take several minutes.
        try:
            error_cells = sheet.UsedRange.SpecialCells(XL_CELL_TYPE_FORMULAS, XL_ERRORS)
        except Exception:
            error_cells = None
        if error_cells is not None:
            for cell in error_cells.Cells:
                text = str(cell.Text)
                formula_errors.append(
                    CellData(
                        sheet=sheet_name,
                        address=str(cell.Address).replace("$", ""),
                        value=json_safe(cell.Value2),
                        display_value=text,
                        formula=str(cell.Formula) if cell.HasFormula else None,
                    )
                )

    return WorkbookValidation(
        path=str(session.path),
        valid=not missing and not formula_errors,
        required_sheets_missing=missing,
        formula_errors=formula_errors,
    )
