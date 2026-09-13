from __future__ import annotations

from typing import Any

from .models import SheetInspection, WorkbookInspection
from .session import ExcelSession


XL_CELL_TYPE_FORMULAS = -4123
XL_SHEET_VISIBLE = -1
XL_SHEET_HIDDEN = 0
XL_SHEET_VERY_HIDDEN = 2


def _visibility(value: int) -> str:
    return {
        XL_SHEET_VISIBLE: "visible",
        XL_SHEET_HIDDEN: "hidden",
        XL_SHEET_VERY_HIDDEN: "very_hidden",
    }.get(value, f"unknown:{value}")


def _formula_count(used_range: Any) -> int:
    try:
        return int(used_range.SpecialCells(XL_CELL_TYPE_FORMULAS).Count)
    except Exception:
        return 0


def inspect_workbook(session: ExcelSession) -> WorkbookInspection:
    sheets: list[SheetInspection] = []
    warnings: list[str] = []
    for index in range(1, session.workbook.Worksheets.Count + 1):
        sheet = session.workbook.Worksheets(index)
        used = sheet.UsedRange
        sheets.append(
            SheetInspection(
                name=str(sheet.Name),
                index=index,
                visibility=_visibility(int(sheet.Visible)),
                used_range=str(used.Address).replace("$", ""),
                rows=int(used.Rows.Count),
                columns=int(used.Columns.Count),
                formula_cells=_formula_count(used),
                shape_count=int(sheet.Shapes.Count),
                table_count=int(sheet.ListObjects.Count),
            )
        )
    external_links: list[str] = []
    try:
        links = session.workbook.LinkSources(1)
        if links:
            external_links = [str(item) for item in links]
    except Exception as error:
        warnings.append(f"Unable to inspect external links: {error}")
    return WorkbookInspection(
        path=str(session.path),
        extension=session.path.suffix.lower(),
        sheet_count=len(sheets),
        sheets=sheets,
        external_links=external_links,
        warnings=warnings,
    )
