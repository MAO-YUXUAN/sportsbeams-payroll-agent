from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from .errors import ExcelDependencyError, ExcelInputError, ExcelOperationError


SUPPORTED_EXTENSIONS = {".xls", ".xlsx", ".xlsm", ".xlsb"}


def resolve_excel_path(path: str | Path, *, must_exist: bool = True) -> Path:
    candidate = Path(path).expanduser().resolve()
    if candidate.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ExcelInputError(f"Unsupported Excel file type: {candidate.suffix}")
    if must_exist and not candidate.is_file():
        raise ExcelInputError(f"Excel file does not exist: {candidate}")
    return candidate


class ExcelSession(AbstractContextManager["ExcelSession"]):
    """Own one isolated Excel process and guarantee workbook/process cleanup."""

    def __init__(self, path: str | Path, *, read_only: bool = True, update_links: bool = False):
        self.path = resolve_excel_path(path)
        self.read_only = read_only
        self.update_links = update_links
        self.app: Any = None
        self.workbook: Any = None

    def __enter__(self) -> "ExcelSession":
        try:
            import pythoncom
            import win32com.client
        except ImportError as error:
            raise ExcelDependencyError("pywin32 is required for Excel COM tools") from error

        pythoncom.CoInitialize()
        self._pythoncom = pythoncom
        try:
            self.app = win32com.client.DispatchEx("Excel.Application")
            self.app.Visible = False
            self.app.DisplayAlerts = False
            self.app.ScreenUpdating = False
            self.app.EnableEvents = False
            self.app.AskToUpdateLinks = False
            self.workbook = self.app.Workbooks.Open(
                str(self.path),
                UpdateLinks=1 if self.update_links else 0,
                ReadOnly=self.read_only,
                IgnoreReadOnlyRecommended=True,
                Notify=False,
                AddToMru=False,
            )
            return self
        except Exception as error:
            self.close()
            raise ExcelOperationError(f"Unable to open workbook: {self.path}") from error

    def close(self, *, save_changes: bool = False) -> None:
        if self.workbook is not None:
            try:
                self.workbook.Close(SaveChanges=save_changes)
            except Exception:
                pass
            self.workbook = None
        if self.app is not None:
            try:
                self.app.Quit()
            except Exception:
                pass
            self.app = None
        pythoncom = getattr(self, "_pythoncom", None)
        if pythoncom is not None:
            pythoncom.CoUninitialize()
            self._pythoncom = None

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close(save_changes=False)

    def sheet(self, name: str) -> Any:
        try:
            return self.workbook.Worksheets(name)
        except Exception as error:
            raise ExcelInputError(f"Worksheet does not exist: {name}") from error
