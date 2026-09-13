from __future__ import annotations

from typing import Any

from .models import CellData, json_safe
from .session import ExcelSession


def _matrix(value: Any, rows: int, columns: int) -> list[list[Any]]:
    if rows == 1 and columns == 1:
        return [[json_safe(value)]]
    if rows == 1:
        return [[json_safe(item) for item in value]]
    if columns == 1:
        return [[json_safe(item)] for item in value]
    return [[json_safe(item) for item in row] for row in value]


def list_sheets(session: ExcelSession) -> list[str]:
    return [str(session.workbook.Worksheets(index).Name) for index in range(1, session.workbook.Worksheets.Count + 1)]


def read_range(session: ExcelSession, sheet_name: str, address: str) -> list[list[Any]]:
    target = session.sheet(sheet_name).Range(address)
    return _matrix(target.Value2, target.Rows.Count, target.Columns.Count)


def read_formulas(session: ExcelSession, sheet_name: str, address: str) -> list[list[Any]]:
    target = session.sheet(sheet_name).Range(address)
    return _matrix(target.Formula, target.Rows.Count, target.Columns.Count)


def read_cell(session: ExcelSession, sheet_name: str, address: str) -> CellData:
    target = session.sheet(sheet_name).Range(address)
    formula = target.Formula if target.HasFormula else None
    return CellData(
        sheet=sheet_name,
        address=str(target.Address).replace("$", ""),
        value=json_safe(target.Value2),
        display_value=str(target.Text),
        formula=str(formula) if formula is not None else None,
    )
