from __future__ import annotations

from typing import Any

from .models import FormulaChange
from .session import ExcelSession


XL_CELL_TYPE_FORMULAS = -4123


def iter_formula_cells(session: ExcelSession, sheet_name: str) -> list[Any]:
    used = session.sheet(sheet_name).UsedRange
    try:
        formula_range = used.SpecialCells(XL_CELL_TYPE_FORMULAS)
    except Exception:
        return []
    cells: list[Any] = []
    for area_index in range(1, formula_range.Areas.Count + 1):
        area = formula_range.Areas(area_index)
        for cell_index in range(1, area.Cells.Count + 1):
            cells.append(area.Cells(cell_index))
    return cells


def propose_formula_replacement(
    session: ExcelSession,
    sheet_name: str,
    old_text: str,
    new_text: str,
    *,
    reason: str,
) -> list[FormulaChange]:
    if not old_text or old_text == new_text:
        return []
    changes: list[FormulaChange] = []
    for cell in iter_formula_cells(session, sheet_name):
        formula = str(cell.Formula)
        if old_text in formula:
            changes.append(
                FormulaChange(
                    sheet=sheet_name,
                    address=str(cell.Address).replace("$", ""),
                    old_formula=formula,
                    new_formula=formula.replace(old_text, new_text),
                    reason=reason,
                )
            )
    return changes


def apply_formula_changes(session: ExcelSession, changes: list[FormulaChange]) -> None:
    for change in changes:
        cell = session.sheet(change.sheet).Range(change.address)
        if str(cell.Formula) != change.old_formula:
            raise ValueError(f"Formula changed after preview: {change.sheet}!{change.address}")
        cell.Formula = change.new_formula
