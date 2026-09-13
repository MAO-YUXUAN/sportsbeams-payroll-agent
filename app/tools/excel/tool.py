from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from .formula import apply_formula_changes, propose_formula_replacement
from .inspector import inspect_workbook as inspect_open_workbook
from .models import FormulaChange, ToolResult
from .reader import read_cell as read_open_cell
from .reader import read_formulas as read_open_formulas
from .reader import read_range as read_open_range
from .session import ExcelSession
from .validator import validate_workbook as validate_open_workbook
from .writer import copy_and_rename_sheet, prepare_output_copy, save_as_xlsx, sha256_file, write_cell


class ExcelTool:
    """Stable, auditable facade for Agent-facing Excel operations."""

    def inspect_workbook(self, path: str | Path) -> ToolResult[dict[str, Any]]:
        with ExcelSession(path, read_only=True) as session:
            inspection = inspect_open_workbook(session)
        return ToolResult(True, "inspect_workbook", asdict(inspection), inspection.warnings)

    def read_cell(self, path: str | Path, sheet: str, address: str) -> ToolResult[dict[str, Any]]:
        with ExcelSession(path, read_only=True) as session:
            cell = read_open_cell(session, sheet, address)
        return ToolResult(True, "read_cell", asdict(cell))

    def read_range(self, path: str | Path, sheet: str, address: str) -> ToolResult[list[list[Any]]]:
        with ExcelSession(path, read_only=True) as session:
            data = read_open_range(session, sheet, address)
        return ToolResult(True, "read_range", data)

    def read_formulas(self, path: str | Path, sheet: str, address: str) -> ToolResult[list[list[Any]]]:
        with ExcelSession(path, read_only=True) as session:
            data = read_open_formulas(session, sheet, address)
        return ToolResult(True, "read_formulas", data)

    def validate_workbook(
        self,
        path: str | Path,
        required_sheets: list[str] | None = None,
    ) -> ToolResult[dict[str, Any]]:
        with ExcelSession(path, read_only=True) as session:
            validation = validate_open_workbook(session, required_sheets)
        return ToolResult(validation.valid, "validate_workbook", asdict(validation))

    def preview_formula_replacement(
        self,
        path: str | Path,
        sheet: str,
        old_text: str,
        new_text: str,
        reason: str,
    ) -> ToolResult[list[dict[str, Any]]]:
        with ExcelSession(path, read_only=True) as session:
            changes = propose_formula_replacement(session, sheet, old_text, new_text, reason=reason)
        return ToolResult(True, "preview_formula_replacement", [asdict(change) for change in changes])

    def save_as_xlsx(self, source_path: str | Path, output_path: str | Path) -> ToolResult[dict[str, Any]]:
        saved_path, source_hash = save_as_xlsx(source_path, output_path)
        with ExcelSession(saved_path, read_only=True) as session:
            inspection = inspect_open_workbook(session)
        return ToolResult(
            True,
            "save_as_xlsx",
            {
                "source_path": str(Path(source_path).resolve()),
                "source_sha256": source_hash,
                "output_path": str(saved_path),
                "output_sha256": sha256_file(saved_path),
                "inspection": asdict(inspection),
            },
            inspection.warnings,
        )

    def rollover_sheet(
        self,
        source_path: str | Path,
        output_path: str | Path,
        source_sheet: str,
        target_sheet: str,
        title_updates: dict[str, Any] | None = None,
        formula_changes: list[FormulaChange] | None = None,
        *,
        dry_run: bool = True,
    ) -> ToolResult[dict[str, Any]]:
        proposed = {
            "source_path": str(Path(source_path).resolve()),
            "output_path": str(Path(output_path).resolve()),
            "source_sheet": source_sheet,
            "target_sheet": target_sheet,
            "title_updates": title_updates or {},
            "formula_change_count": len(formula_changes or []),
            "dry_run": dry_run,
        }
        if dry_run:
            with ExcelSession(source_path, read_only=True) as session:
                session.sheet(source_sheet)
                if any(str(session.workbook.Worksheets(index).Name) == target_sheet for index in range(1, session.workbook.Worksheets.Count + 1)):
                    raise ValueError(f"Target worksheet already exists: {target_sheet}")
            return ToolResult(True, "rollover_sheet", proposed)

        _, copied_path = prepare_output_copy(source_path, output_path)
        try:
            with ExcelSession(copied_path, read_only=False) as session:
                copy_and_rename_sheet(session, source_sheet, target_sheet)
                for address, value in (title_updates or {}).items():
                    write_cell(session, target_sheet, address, value)
                apply_formula_changes(session, formula_changes or [])
                session.workbook.Save()
            proposed["output_path"] = str(copied_path)
            return ToolResult(True, "rollover_sheet", proposed)
        except Exception:
            if copied_path.exists():
                copied_path.unlink()
            raise
