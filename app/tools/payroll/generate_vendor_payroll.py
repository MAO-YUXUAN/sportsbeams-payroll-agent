from __future__ import annotations

from pathlib import Path
from typing import Any

from app.tools.excel.errors import ExcelInputError, ExcelOperationError
from app.tools.excel.models import ToolResult


XL_OPEN_XML_WORKBOOK = 51


class VendorPayrollGenerateTool:
    """Generate the payroll workbooks sent to Yicai (15) and Yarun (16)."""

    def generate(self, payroll_path: str | Path, output_path: str | Path, *, vendor: str,
                 expense_month: str, employees: list[Any]) -> ToolResult[dict]:
        source = Path(payroll_path).expanduser().resolve()
        output = Path(output_path).expanduser().resolve()
        if not source.is_file():
            raise ExcelInputError(f"Payroll workbook does not exist: {source}")
        if output.exists():
            raise ExcelInputError(f"Vendor payroll output already exists: {output}")
        if output.suffix.casefold() != ".xlsx":
            raise ExcelInputError("Vendor payroll output must be .xlsx")
        vendor_id = vendor.casefold()
        if vendor_id not in {"yicai", "yarun"}:
            raise ExcelInputError(f"Unsupported payroll vendor: {vendor}")
        output.parent.mkdir(parents=True, exist_ok=True)
        result = self._write(source, output, vendor_id, expense_month, employees)
        return ToolResult(True, "generate_vendor_payroll", result)

    @staticmethod
    def _write(source: Path, output: Path, vendor: str, expense_month: str, employees: list[Any]) -> dict:
        try:
            import pythoncom
            import win32com.client
        except ImportError as error:
            raise ExcelOperationError("pywin32 is required to generate vendor payroll workbooks") from error
        pythoncom.CoInitialize(); app = source_book = target_book = None
        try:
            app = win32com.client.DispatchEx("Excel.Application")
            app.Visible = False; app.DisplayAlerts = False; app.ScreenUpdating = False; app.EnableEvents = False
            source_book = app.Workbooks.Open(str(source), UpdateLinks=0, ReadOnly=True, IgnoreReadOnlyRecommended=True)
            month = int(expense_month.split("-")[1]); source_sheet = source_book.Worksheets(f"{month}月")
            target_book = app.Workbooks.Add(); sheet = target_book.Worksheets(1)
            aliases = {"李伟1": "李伟", "杨悦1": "杨悦", "刘同涛1": "刘同涛", "孙晓霞1": "孙晓霞"}
            source_rows = {}
            for row in range(8, 39):
                raw = str(source_sheet.Range(f"C{row}").Text).strip(); name = aliases.get(raw, raw)
                if name: source_rows[name] = row
            if vendor == "yicai":
                sheet.Name = f"{month}月派遣"
                source_sheet.Range("A1:AJ3").Copy(sheet.Range("A1"))
                selected = []
                for target_row, name in enumerate(employees, start=4):
                    if name not in source_rows: raise ExcelOperationError(f"Yicai employee was not found in main payroll: {name}")
                    source_sheet.Range(f"A{source_rows[name]}:AJ{source_rows[name]}").Copy(sheet.Range(f"A{target_row}"))
                    selected.append(name)
                sheet.Columns("A:AJ").AutoFit()
            else:
                sheet.Name = f"{month}月"; selected = []
                headers = (("序号", "姓名", "身份证", "应发工资", "个人养老", "个人医疗", "个人失业", "个人公积金", "四金小计", "应税工资", "个税", "实发", "代发服务费", "备注", "开户行", "卡号"),)
                sheet.Range("A1:P1").Merge(); sheet.Range("A1").Value2 = "薪资表（代发工资）-"
                sheet.Range("A2").Value2 = "单位：元/人民币"; sheet.Range("A3:P3").Value2 = headers
                for target_row, item in enumerate(employees, start=5):
                    name = item["employee_name"]
                    if name not in source_rows: raise ExcelOperationError(f"Yarun employee was not found in main payroll: {name}")
                    row = source_rows[name]
                    values = (target_row - 4, name, item["employee_id"], source_sheet.Range(f"P{row}").Value2,
                              source_sheet.Range(f"AA{row}").Value2, source_sheet.Range(f"AB{row}").Value2,
                              source_sheet.Range(f"AC{row}").Value2, source_sheet.Range(f"AD{row}").Value2,
                              source_sheet.Range(f"AE{row}").Value2, source_sheet.Range(f"P{row}").Value2 - source_sheet.Range(f"AE{row}").Value2,
                              source_sheet.Range(f"AI{row}").Value2, source_sheet.Range(f"AJ{row}").Value2, 0, "",
                              item["bank_name"], item["bank_account"])
                    sheet.Range(f"A{target_row}:P{target_row}").Value2 = (values,); selected.append(name)
                total_row = len(employees) + 5; sheet.Range(f"A{total_row}:C{total_row}").Merge(); sheet.Range(f"A{total_row}").Value2 = "汇总"
                for col in "DEFGHIJKLM": sheet.Range(f"{col}{total_row}").Formula = f"=SUM({col}5:{col}{total_row-1})"
                sheet.Range(f"A3:P{total_row}").Borders.LineStyle = 1; sheet.Range("A1:P1").Font.Bold = True
                sheet.Range("A3:P3").Font.Bold = True; sheet.Columns("A:P").AutoFit()
            target_book.SaveAs(str(output), FileFormat=XL_OPEN_XML_WORKBOOK)
            return {"vendor": vendor, "path": str(output), "expense_month": expense_month, "record_count": len(selected), "employees": selected}
        finally:
            if target_book is not None:
                try: target_book.Close(SaveChanges=False)
                except Exception: pass
            if source_book is not None:
                try: source_book.Close(SaveChanges=False)
                except Exception: pass
            if app is not None:
                try: app.Quit()
                except Exception: pass
            pythoncom.CoUninitialize()
