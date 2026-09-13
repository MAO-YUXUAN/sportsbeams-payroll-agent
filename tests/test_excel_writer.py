from pathlib import Path

import pytest

from app.tools.excel.errors import ExcelInputError
from app.tools.excel.writer import prepare_output_copy, resolve_xlsx_output


def test_output_copy_cannot_overwrite_source(tmp_path: Path) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"test")
    with pytest.raises(ExcelInputError):
        prepare_output_copy(source, source)


def test_output_copy_preserves_source(tmp_path: Path) -> None:
    source = tmp_path / "source.xlsx"
    output = tmp_path / "output.xlsx"
    source.write_bytes(b"test")
    prepare_output_copy(source, output)
    assert source.read_bytes() == b"test"
    assert output.read_bytes() == b"test"


def test_xlsx_output_is_required(tmp_path: Path) -> None:
    source = tmp_path / "source.xls"
    source.write_bytes(b"test")
    with pytest.raises(ExcelInputError):
        resolve_xlsx_output(source, tmp_path / "output.xls")


def test_xlsx_output_rejects_macro_workbook(tmp_path: Path) -> None:
    source = tmp_path / "source.xlsm"
    source.write_bytes(b"test")
    with pytest.raises(ExcelInputError):
        resolve_xlsx_output(source, tmp_path / "output.xlsx")
