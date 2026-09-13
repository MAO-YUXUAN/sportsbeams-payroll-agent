from __future__ import annotations

from pathlib import Path

import pythoncom
import win32com.client


XL_OPEN_XML_WORKBOOK = 51


def clear_contents(sheet, address: str) -> None:
    target = sheet.Range(address)
    if target.MergeCells:
        target.MergeArea.ClearContents()
    else:
        target.ClearContents()


def build_templates(project_root: Path, source_root: Path) -> list[Path]:
    target_root = project_root / "templates" / "internal-payments"
    target_root.mkdir(parents=True, exist_ok=True)
    jobs = (
        (
            "19.7月份社保明细 - 赛倍明.xlsx",
            "付款单",
            "default-social-payment-request.xlsx",
            ("C4", "C6", "C8", "C9", "C13", "D14", "D15"),
        ),
        (
            "20.2026个税付款单.xlsx",
            "5",
            "default-tax-payment-request.xlsx",
            ("C5", "C7", "C9", "C10", "C14", "D15", "D16", "K8:O8"),
        ),
        (
            "21.7月份公积金-赛倍明.xlsx",
            "付款单",
            "default-housing-payment-request.xlsx",
            ("C4", "C6", "C8", "C9", "C13", "D14", "D15"),
        ),
    )
    pythoncom.CoInitialize()
    excel = None
    outputs: list[Path] = []
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.ScreenUpdating = False
        excel.EnableEvents = False
        excel.AskToUpdateLinks = False
        for source_name, sheet_name, target_name, addresses in jobs:
            source_book = template_book = None
            try:
                source_book = excel.Workbooks.Open(
                    str(source_root / source_name),
                    UpdateLinks=0,
                    ReadOnly=True,
                    IgnoreReadOnlyRecommended=True,
                )
                source_book.Worksheets(sheet_name).Copy()
                template_book = excel.ActiveWorkbook
                sheet = template_book.Worksheets(1)
                sheet.Name = "付款单"
                for address in addresses:
                    clear_contents(sheet, address)
                uncleared = [
                    cell.Address(False, False)
                    for address in addresses
                    for cell in sheet.Range(address).Cells
                    if str(cell.Text).strip()
                ]
                if uncleared:
                    raise RuntimeError(f"Template cells were not cleared: {target_name}: {uncleared}")
                output = target_root / target_name
                template_book.SaveAs(str(output), FileFormat=XL_OPEN_XML_WORKBOOK)
                outputs.append(output)
            finally:
                if template_book is not None:
                    template_book.Close(SaveChanges=False)
                if source_book is not None:
                    source_book.Close(SaveChanges=False)
    finally:
        if excel is not None:
            excel.Quit()
        pythoncom.CoUninitialize()
    return outputs


if __name__ == "__main__":
    project = Path(__file__).resolve().parents[1]
    source = project.parent / "赛倍明工资核算步骤及所需要文件" / "所需要文件"
    for path in build_templates(project, source):
        print(path)
