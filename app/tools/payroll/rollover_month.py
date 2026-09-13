from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.tools.excel.errors import ExcelInputError, ExcelOperationError
from app.tools.excel.formula import apply_formula_changes, propose_formula_replacement
from app.tools.excel.inspector import inspect_workbook
from app.tools.excel.models import FormulaChange, ToolResult
from app.tools.excel.reader import read_cell
from app.tools.excel.session import ExcelSession
from app.tools.excel.validator import validate_workbook
from app.tools.excel.writer import (
    copy_and_rename_sheet,
    resolve_xlsx_output,
    save_as_xlsx,
    sha256_file,
    write_cell,
)


class PayrollRolloverTool:
    """Create a new monthly payroll workbook from an approved prior workbook."""

    def rollover_payroll_month(
        self,
        source_path: str | Path,
        output_path: str | Path,
        source_sheet: str,
        target_sheet: str,
        title_cell: str,
        target_title: str,
        formula_replacements: list[dict[str, str]] | None = None,
        *,
        copy_position: str = "after",
        dry_run: bool = True,
    ) -> ToolResult[dict[str, Any]]:
        replacements = formula_replacements or []
        if copy_position not in {"before", "after"}:
            raise ExcelInputError("Sheet copy position must be 'before' or 'after'")
        if not source_sheet or not target_sheet:
            raise ExcelInputError("Source and target worksheet names are required")
        if source_sheet == target_sheet:
            raise ExcelInputError("Source and target worksheet names must differ")
        if not title_cell or not target_title:
            raise ExcelInputError("Title cell and target title are required")
        source, output = resolve_xlsx_output(source_path, output_path)
        source_hash = sha256_file(source)

        preview_changes: list[FormulaChange] = []
        with ExcelSession(source, read_only=True) as session:
            source_worksheet = session.sheet(source_sheet)
            source_structure = self._worksheet_structure(source_worksheet)
            sheet_names = [
                str(session.workbook.Worksheets(index).Name)
                for index in range(1, session.workbook.Worksheets.Count + 1)
            ]
            if target_sheet in sheet_names:
                raise ExcelInputError(f"Target worksheet already exists: {target_sheet}")
            current_title = read_cell(session, source_sheet, title_cell)
            for replacement in replacements:
                self._validate_replacement(replacement)
                proposed = propose_formula_replacement(
                    session,
                    source_sheet,
                    replacement["old"],
                    replacement["new"],
                    reason=replacement["reason"],
                )
                preview_changes.extend(
                    FormulaChange(
                        sheet=target_sheet,
                        address=change.address,
                        old_formula=change.old_formula,
                        new_formula=change.new_formula,
                        reason=change.reason,
                    )
                    for change in proposed
                )

        plan = {
            "status": "preview" if dry_run else "executing",
            "source_path": str(source),
            "source_sha256": source_hash,
            "output_path": str(output),
            "source_sheet": source_sheet,
            "target_sheet": target_sheet,
            "copy_position": copy_position,
            "title_change": {
                "cell": title_cell,
                "old_value": current_title.value,
                "new_value": target_title,
            },
            "formula_changes": [asdict(change) for change in preview_changes],
        }
        if dry_run:
            return ToolResult(True, "rollover_payroll_month", plan)

        saved_output: Path | None = None
        try:
            saved_output, _ = save_as_xlsx(source, output)
            applied_changes: list[FormulaChange] = []
            with ExcelSession(saved_output, read_only=False) as session:
                copy_and_rename_sheet(
                    session,
                    source_sheet,
                    target_sheet,
                    position=copy_position,
                )
                write_cell(session, target_sheet, title_cell, target_title)
                for replacement in replacements:
                    applied_changes.extend(
                        propose_formula_replacement(
                            session,
                            target_sheet,
                            replacement["old"],
                            replacement["new"],
                            reason=replacement["reason"],
                        )
                    )
                apply_formula_changes(session, applied_changes)
                session.workbook.Save()

            with ExcelSession(saved_output, read_only=True) as session:
                interim_sheet_names = [
                    str(session.workbook.Worksheets(index).Name)
                    for index in range(1, session.workbook.Worksheets.Count + 1)
                ]
            missing_source_sheets = [name for name in sheet_names if name not in interim_sheet_names]
            if missing_source_sheets:
                self._restore_missing_sheets(
                    source,
                    saved_output,
                    source_sheet_names=sheet_names,
                    missing_sheet_names=missing_source_sheets,
                )

            with ExcelSession(saved_output, read_only=True) as session:
                inspection = inspect_workbook(session)
                validation = validate_workbook(session, [source_sheet, target_sheet])
                saved_title = read_cell(session, target_sheet, title_cell)
                target_structure = self._worksheet_structure(session.sheet(target_sheet))
                saved_sheet_names = [
                    str(session.workbook.Worksheets(index).Name)
                    for index in range(1, session.workbook.Worksheets.Count + 1)
                ]

            if saved_title.value != target_title:
                raise ExcelOperationError(
                    f"Saved title does not match the requested title: {target_sheet}!{title_cell}"
                )
            if target_structure != source_structure:
                raise ExcelOperationError(
                    "Copied payroll worksheet structure does not match the source "
                    f"worksheet: source={source_structure}, target={target_structure}"
                )
            if not validation.valid:
                raise ExcelOperationError("Rollover workbook failed validation")
            still_missing = [name for name in sheet_names if name not in saved_sheet_names]
            if still_missing:
                raise ExcelOperationError(
                    f"Rollover workbook lost source worksheets: {still_missing}"
                )
            source_index = saved_sheet_names.index(source_sheet)
            target_index = saved_sheet_names.index(target_sheet)
            expected_index = source_index + (1 if copy_position == "after" else -1)
            if target_index != expected_index:
                raise ExcelOperationError(
                    f"Rollover worksheet order is invalid: {target_sheet} is not {copy_position} {source_sheet}"
                )
            if sha256_file(source) != source_hash:
                raise ExcelOperationError("Source workbook changed during rollover")

            plan.update(
                {
                    "status": "completed",
                    "output_sha256": sha256_file(saved_output),
                    "formula_changes": [asdict(change) for change in applied_changes],
                    "inspection": asdict(inspection),
                    "validation": asdict(validation),
                    "sheet_order": saved_sheet_names,
                    "restored_source_sheets": missing_source_sheets,
                }
            )
            return ToolResult(True, "rollover_payroll_month", plan, inspection.warnings)
        except Exception:
            if saved_output is not None and saved_output.exists():
                saved_output.unlink()
            raise

    @staticmethod
    def _worksheet_structure(worksheet: Any) -> dict[str, Any]:
        """Return layout facts that must remain identical in a monthly copy."""
        used = worksheet.UsedRange
        return {
            "rows": int(used.Rows.Count),
            "columns": int(used.Columns.Count),
            "first_row": int(used.Row),
            "first_column": int(used.Column),
        }

    def repair_workbook_structure(
        self,
        source_path: str | Path,
        output_path: str | Path,
        *,
        source_sheet: str,
        target_sheet: str,
    ) -> ToolResult[dict[str, Any]]:
        """Restore source worksheets and normalize order without changing cell values."""
        source = Path(source_path).expanduser().resolve()
        output = Path(output_path).expanduser().resolve()
        if source == output:
            raise ExcelInputError("Repair source and output paths must differ")
        with ExcelSession(source, read_only=True) as session:
            source_sheet_names = [
                str(session.workbook.Worksheets(index).Name)
                for index in range(1, session.workbook.Worksheets.Count + 1)
            ]
        if source_sheet not in source_sheet_names:
            raise ExcelInputError(f"Source worksheet does not exist: {source_sheet}")
        with ExcelSession(output, read_only=True) as session:
            before_names = [
                str(session.workbook.Worksheets(index).Name)
                for index in range(1, session.workbook.Worksheets.Count + 1)
            ]
        if target_sheet not in before_names:
            raise ExcelInputError(f"Target worksheet does not exist: {target_sheet}")

        missing = [name for name in source_sheet_names if name not in before_names]
        if missing:
            self._restore_missing_sheets(
                source,
                output,
                source_sheet_names=source_sheet_names,
                missing_sheet_names=missing,
            )

        expected_order = list(source_sheet_names)
        expected_order.insert(expected_order.index(source_sheet) + 1, target_sheet)
        with ExcelSession(output, read_only=False) as session:
            for name in reversed(expected_order):
                worksheet = session.workbook.Worksheets(name)
                if worksheet.Index != 1:
                    worksheet.Move(Before=session.workbook.Worksheets(1))
            session.workbook.Save()

        with ExcelSession(output, read_only=True) as session:
            after_names = [
                str(session.workbook.Worksheets(index).Name)
                for index in range(1, session.workbook.Worksheets.Count + 1)
            ]
        still_missing = [name for name in expected_order if name not in after_names]
        if still_missing:
            raise ExcelOperationError(f"Workbook repair could not restore worksheets: {still_missing}")
        if after_names[:len(expected_order)] != expected_order:
            raise ExcelOperationError("Workbook repair could not normalize worksheet order")
        return ToolResult(
            True,
            "repair_workbook_structure",
            {
                "status": "repaired" if missing or before_names != after_names else "already_valid",
                "source_path": str(source),
                "output_path": str(output),
                "restored_sheets": missing,
                "before_order": before_names,
                "after_order": after_names,
                "output_sha256": sha256_file(output),
            },
        )

    @staticmethod
    def _restore_missing_sheets(
        source_path: Path,
        output_path: Path,
        *,
        source_sheet_names: list[str],
        missing_sheet_names: list[str],
    ) -> None:
        """Restore any source worksheets Excel omitted while copying a monthly sheet."""
        with ExcelSession(output_path, read_only=False) as destination:
            source_book = destination.app.Workbooks.Open(
                str(source_path),
                UpdateLinks=0,
                ReadOnly=True,
                IgnoreReadOnlyRecommended=True,
                Notify=False,
                AddToMru=False,
            )
            try:
                for missing_name in missing_sheet_names:
                    existing = {
                        str(destination.workbook.Worksheets(index).Name)
                        for index in range(1, destination.workbook.Worksheets.Count + 1)
                    }
                    if missing_name in existing:
                        continue
                    source_index = source_sheet_names.index(missing_name)
                    next_existing = next(
                        (name for name in source_sheet_names[source_index + 1:] if name in existing),
                        None,
                    )
                    source_sheet = source_book.Worksheets(missing_name)
                    if next_existing:
                        source_sheet.Copy(Before=destination.workbook.Worksheets(next_existing))
                    else:
                        source_sheet.Copy(
                            After=destination.workbook.Worksheets(destination.workbook.Worksheets.Count)
                        )
                destination.workbook.Save()
            finally:
                source_book.Close(SaveChanges=False)

    @staticmethod
    def _validate_replacement(replacement: dict[str, str]) -> None:
        required = {"old", "new", "reason"}
        missing = required.difference(replacement)
        if missing:
            raise ExcelInputError(f"Formula replacement is missing fields: {sorted(missing)}")
        if not replacement["old"]:
            raise ExcelInputError("Formula replacement old text cannot be empty")
        if replacement["old"] == replacement["new"]:
            raise ExcelInputError("Formula replacement old and new text must differ")
