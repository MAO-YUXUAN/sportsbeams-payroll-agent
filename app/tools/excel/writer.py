from __future__ import annotations

import shutil
import hashlib
from tempfile import TemporaryDirectory
from pathlib import Path
from typing import Any

from .errors import ExcelInputError
from .models import json_safe
from .session import ExcelSession, resolve_excel_path


XL_OPEN_XML_WORKBOOK = 51


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_xlsx_output(source: str | Path, output: str | Path) -> tuple[Path, Path]:
    source_path = resolve_excel_path(source)
    output_path = Path(output).expanduser().resolve()
    if output_path.suffix.lower() != ".xlsx":
        raise ExcelInputError("Output file must use the .xlsx extension")
    if source_path == output_path:
        raise ExcelInputError("Output path must be different from the source path")
    if output_path.exists():
        raise ExcelInputError(f"Output file already exists: {output_path}")
    if source_path.suffix.lower() in {".xlsm", ".xlsb"}:
        raise ExcelInputError(
            "Macro or binary workbooks cannot be converted to .xlsx because content may be lost"
        )
    return source_path, output_path


def save_as_xlsx(source: str | Path, output: str | Path) -> tuple[Path, str]:
    """Save an Excel workbook as a new XLSX without modifying the source file."""
    source_path, output_path = resolve_xlsx_output(source, output)
    source_hash = sha256_file(source_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if source_path.suffix.lower() == ".xlsx":
            shutil.copy2(source_path, output_path)
        else:
            # Excel may rewrite legacy XLS metadata even when SaveAs targets a new
            # path. Convert a disposable copy so the approved source is never
            # opened by Excel in writable mode.
            with TemporaryDirectory(prefix="sportsbeams-xls-convert-", dir=output_path.parent) as temp_dir:
                conversion_source = Path(temp_dir) / source_path.name
                shutil.copy2(source_path, conversion_source)
                with ExcelSession(conversion_source, read_only=False) as session:
                    session.workbook.SaveAs(str(output_path), FileFormat=XL_OPEN_XML_WORKBOOK)
        if not output_path.is_file():
            raise ExcelInputError(f"Excel did not create the output file: {output_path}")
        if sha256_file(source_path) != source_hash:
            raise ExcelInputError("Source workbook changed during XLSX conversion")
        return output_path, source_hash
    except Exception:
        if output_path.exists():
            output_path.unlink()
        raise


def prepare_output_copy(source: str | Path, output: str | Path) -> tuple[Path, Path]:
    source_path = resolve_excel_path(source)
    output_path = resolve_excel_path(output, must_exist=False)
    if source_path == output_path:
        raise ExcelInputError("Output path must be different from the source path")
    if output_path.exists():
        raise ExcelInputError(f"Output file already exists: {output_path}")
    if output_path.suffix.lower() != source_path.suffix.lower():
        raise ExcelInputError("Output copy must keep the original Excel extension")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, output_path)
    return source_path, output_path


def sheet_exists(session: ExcelSession, sheet_name: str) -> bool:
    try:
        session.workbook.Worksheets(sheet_name)
        return True
    except Exception:
        return False


def copy_and_rename_sheet(
    session: ExcelSession,
    source_sheet: str,
    target_sheet: str,
    *,
    position: str = "after",
) -> None:
    if sheet_exists(session, target_sheet):
        raise ExcelInputError(f"Target worksheet already exists: {target_sheet}")
    if position not in {"before", "after"}:
        raise ExcelInputError("Sheet copy position must be 'before' or 'after'")
    source = session.sheet(source_sheet)
    names_before = {
        str(session.workbook.Worksheets(index).Name)
        for index in range(1, session.workbook.Worksheets.Count + 1)
    }
    if position == "before":
        source.Copy(source, None)
    else:
        source.Copy(None, source)

    # Excel can change COM worksheet indexes after Copy. Looking up the copy as
    # source.Index +/- 1 can therefore select the neighbouring worksheet and
    # rename it (for example, a department summary sheet). Identify the one new
    # worksheet by name instead; this is stable regardless of index changes.
    new_names = [
        str(session.workbook.Worksheets(index).Name)
        for index in range(1, session.workbook.Worksheets.Count + 1)
        if str(session.workbook.Worksheets(index).Name) not in names_before
    ]
    if len(new_names) != 1:
        raise ExcelInputError(
            f"Excel worksheet copy produced an unexpected result: {new_names}"
        )
    copied = session.workbook.Worksheets(new_names[0])
    copied.Name = target_sheet


def write_cell(session: ExcelSession, sheet_name: str, address: str, value: Any) -> None:
    session.sheet(sheet_name).Range(address).Value2 = json_safe(value)


def write_range(session: ExcelSession, sheet_name: str, address: str, values: list[list[Any]]) -> None:
    target = session.sheet(sheet_name).Range(address)
    rows = len(values)
    columns = len(values[0]) if rows else 0
    if rows != target.Rows.Count or columns != target.Columns.Count:
        raise ExcelInputError(
            f"Data shape {rows}x{columns} does not match target range "
            f"{target.Rows.Count}x{target.Columns.Count}"
        )
    if any(len(row) != columns for row in values):
        raise ExcelInputError("All rows must have the same number of columns")
    target.Value2 = tuple(tuple(json_safe(item) for item in row) for row in values)
