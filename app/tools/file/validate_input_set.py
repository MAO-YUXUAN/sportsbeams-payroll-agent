from __future__ import annotations

from datetime import date
from pathlib import Path

from app.tools.excel.errors import ExcelInputError
from app.tools.excel.models import ToolResult
from app.tools.excel.session import ExcelSession

from .catalog import load_file_catalog


class InputSetValidationTool:
    """Validate category completeness and optionally open workbooks read-only."""

    def validate(
        self,
        discovered_files: list[dict],
        catalog_path: str | Path,
        *,
        expense_month: str,
        inspect_workbooks: bool = True,
    ) -> ToolResult[dict]:
        try:
            year, month = (int(part) for part in expense_month.split("-", 1))
            month_date = date(year, month, 1)
        except (TypeError, ValueError) as error:
            raise ExcelInputError("expense_month must use YYYY-MM format") from error
        categories = load_file_catalog(catalog_path)
        grouped: dict[str, list[dict]] = {category.id: [] for category in categories}
        ambiguous = []
        for item in discovered_files:
            matches = item.get("matched_categories", [])
            if len(matches) > 1:
                ambiguous.append(item)
            category_id = item.get("category_id")
            if category_id in grouped:
                grouped[category_id].append(item)

        missing = [category.id for category in categories if category.required and not grouped[category.id]]
        required_ids = {category.id for category in categories if category.required}
        duplicates = {key: value for key, value in grouped.items() if key in required_ids and len(value) > 1}
        workbook_errors = []
        inspected = []
        context = {"expense_yyyymm": month_date.strftime("%Y%m")}
        if inspect_workbooks:
            by_id = {category.id: category for category in categories}
            for category_id, items in grouped.items():
                category = by_id[category_id]
                required_sheets = list(category.required_sheets)
                if category.month_sheet:
                    required_sheets.append(category.month_sheet.format(**context))
                for item in items:
                    try:
                        with ExcelSession(item["path"], read_only=True) as session:
                            sheet_names = [str(session.workbook.Worksheets(index).Name) for index in range(1, session.workbook.Worksheets.Count + 1)]
                            absent = [name for name in required_sheets if name not in sheet_names]
                            if absent:
                                workbook_errors.append({"path": item["path"], "missing_sheets": absent})
                            if category.month_check and not absent:
                                check = category.month_check
                                month_sheet = session.sheet(str(check["sheet"]))
                                start_row = int(check["start_row"])
                                final_row = int(month_sheet.UsedRange.Row + month_sheet.UsedRange.Rows.Count - 1)
                                expected = month_date.strftime("%Y%m")
                                found = {
                                    str(month_sheet.Range(f"{check['column']}{row}").Text).strip().replace("-", "")
                                    for row in range(start_row, final_row + 1)
                                }
                                if expected not in found:
                                    workbook_errors.append(
                                        {"path": item["path"], "expense_month_not_found": expense_month}
                                    )
                            inspected.append({"path": item["path"], "sheet_count": len(sheet_names)})
                    except Exception as error:
                        workbook_errors.append({"path": item["path"], "error": str(error)})

        errors = []
        if missing:
            errors.append(f"Missing required categories: {missing}")
        if duplicates:
            errors.append(f"Multiple files found for categories: {sorted(duplicates)}")
        if ambiguous:
            errors.append("Some files match multiple categories")
        if workbook_errors:
            errors.append("Some workbooks failed structure/readability validation")
        return ToolResult(
            not errors,
            "validate_input_set",
            {
                "expense_month": expense_month,
                "status": "passed" if not errors else "blocked",
                "missing_required": missing,
                "duplicates": duplicates,
                "ambiguous": ambiguous,
                "workbook_errors": workbook_errors,
                "inspected": inspected,
                "files_by_category": grouped,
            },
            errors=errors,
        )
