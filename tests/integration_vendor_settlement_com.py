"""Manual Microsoft Excel COM integration test for the vendor settlement workflow."""

from __future__ import annotations

import tempfile
from pathlib import Path

import win32com.client

from app.tools.excel.writer import sha256_file
from app.tools.payroll import ProcessVendorSettlementTool, SettlementMapping, SummaryMapping


def create_workbook(path: Path, sheet_name: str, values: dict[str, object]) -> None:
    excel = win32com.client.DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    workbook = excel.Workbooks.Add()
    try:
        sheet = workbook.Worksheets(1)
        sheet.Name = sheet_name
        for address, value in values.items():
            sheet.Range(address).Value2 = value
        workbook.SaveAs(str(path), FileFormat=51)
    finally:
        workbook.Close(SaveChanges=False)
        excel.Quit()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="sportsbeams-vendor-integration-") as temp_dir:
        root = Path(temp_dir)
        settlement = root / "settlement.xlsx"
        payroll = root / "payroll.xlsx"
        payment_template = root / "payment-template.xlsx"
        payroll_output = root / "payroll-output.xlsx"
        payment_output = root / "payment-output.xlsx"

        create_workbook(
            settlement,
            "结算明细",
            {
                "A1": "员工编号", "B1": "姓名", "C1": "公积金公司", "D1": "公积金个人", "E1": "服务费", "F1": "合计",
                "A2": "E001", "B2": "甲", "C2": 700, "D2": 700, "E2": 100, "F2": 1500,
                "A3": "E002", "B3": "乙", "C3": 800, "D3": 800, "E3": 100, "F3": 1700,
            },
        )
        create_workbook(
            payroll,
            "各部门统计表",
            {"A1": "员工编号", "B1": "姓名", "A2": "E001", "B2": "甲", "A3": "E002", "B3": "乙"},
        )
        create_workbook(payment_template, "付款申请", {"A1": "供应商", "A2": "所属月", "A3": "金额", "A4": "大写", "A5": "用途"})

        source_hashes = {path: sha256_file(path) for path in (settlement, payroll, payment_template)}
        tool = ProcessVendorSettlementTool()
        kwargs = dict(
            settlement_path=settlement,
            payroll_path=payroll,
            payroll_output_path=payroll_output,
            payment_template_path=payment_template,
            payment_output_path=payment_output,
            vendor="测试供应商",
            expense_month="2026-08",
            settlement_mapping=SettlementMapping(
                sheet_name="结算明细",
                start_row=2,
                end_row=3,
                columns={
                    "employee_id": "A", "employee_name": "B", "housing_fund_company": "C",
                    "housing_fund_employee": "D", "service_fee": "E", "row_total": "F",
                },
            ),
            summary_mapping=SummaryMapping(
                sheet_name="各部门统计表",
                start_row=2,
                end_row=3,
                employee_id_column="A",
                employee_name_column="B",
                field_columns={"housing_fund_company": "C", "housing_fund_employee": "D", "service_fee": "E"},
            ),
            payment_sheet="付款申请",
            payment_cell_mapping={
                "vendor_name": "B1", "expense_month": "B2", "payment_amount": "B3",
                "amount_uppercase": "B4", "purpose": "B5",
            },
            payment_values={"purpose": "测试公积金及服务费"},
            reconciliation_fields=["housing_fund_company", "housing_fund_employee", "service_fee"],
        )

        preview = tool.process(**kwargs, dry_run=True)
        assert preview.success
        assert not payroll_output.exists() and not payment_output.exists()
        result = tool.process(**kwargs, dry_run=False)
        assert result.success
        assert result.data["reconciliation"]["status"] == "passed"
        assert result.data["reconciliation"]["settlement_total"] == "3200.00"
        assert payroll_output.exists() and payment_output.exists()
        for path, original_hash in source_hashes.items():
            assert sha256_file(path) == original_hash

    print("Vendor settlement COM integration passed")


if __name__ == "__main__":
    main()
