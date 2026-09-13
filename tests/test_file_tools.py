from hashlib import sha256
from pathlib import Path

from app.tools.file import FileDiscoveryTool, InputSetValidationTool, RunFilePreparationTool


CATALOG = Path(__file__).parents[1] / "knowledge" / "file-catalog.yaml"


def _make_minimum_input_set(folder: Path) -> None:
    names = [
        "1-4、6、9.2026年7月份赛倍明工资表.xls",
        "5.亚润202607结算表.xlsx",
        "7.11.社保公积金代缴金付款单（易才亚润）.xls",
        "8.【派遣】上海赛倍明 社保账单202608.xlsx",
    ]
    for index, name in enumerate(names):
        (folder / name).write_bytes(f"file-{index}".encode())


def test_discover_classifies_real_filename_shapes(tmp_path: Path) -> None:
    _make_minimum_input_set(tmp_path)

    result = FileDiscoveryTool().discover(tmp_path, CATALOG)

    assert result.success
    assert result.data["classified_count"] == 4
    assert {item["category_id"] for item in result.data["files"]} == {
        "payroll_workbook",
        "yarun_settlement",
        "yicai_dispatch_settlement",
        "payment_request_template",
    }


def test_discover_classifies_operational_filenames_without_catalog_numbers(tmp_path: Path) -> None:
    names_and_categories = {
        "亚润202607结算表.xlsx": "yarun_settlement",
        "上海赛倍明派遣社保账单202608.xlsx": "yicai_dispatch_settlement",
        "赛倍明考勤月度汇总.xlsx": "attendance_summary",
        "易才派遣工资表.xlsx": "yicai_payroll",
        "亚润薪资表.xlsx": "yarun_payroll",
        "易才工资账单.xlsx": "payroll_bill",
        "亚润回传工资表.xlsx": "payroll_export",
        "员工社保明细.xlsx": "social_detail",
        "个税付款资料.xlsx": "tax_payment",
        "员工公积金明细.xlsx": "housing_fund_detail",
    }
    for name in names_and_categories:
        (tmp_path / name).write_bytes(b"test")

    result = FileDiscoveryTool().discover(tmp_path, CATALOG)
    actual = {item["name"]: item["category_id"] for item in result.data["files"]}

    assert result.success
    assert actual == names_and_categories


def test_validate_and_prepare_month_run(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_minimum_input_set(source)
    discovery = FileDiscoveryTool().discover(source, CATALOG)
    validation = InputSetValidationTool().validate(
        discovery.data["files"], CATALOG, expense_month="2026-07", inspect_workbooks=False
    )

    assert validation.success
    prepared = RunFilePreparationTool().prepare(validation, tmp_path / "runs", expense_month="2026-07")
    run_root = Path(prepared.data["run_root"])

    assert prepared.data["file_count"] == 4
    assert (run_root / "manifest.json").is_file()
    assert (run_root / "output" / "payroll").is_dir()
    assert (run_root / "output" / "payment_requests").is_dir()
    copied = next((run_root / "input" / "source" / "vendors" / "yarun").iterdir())
    original = next(source.glob("5.*"))
    assert sha256(copied.read_bytes()).hexdigest() == sha256(original.read_bytes()).hexdigest()


def test_validation_reports_missing_required_categories(tmp_path: Path) -> None:
    (tmp_path / "5.亚润202607结算表.xlsx").write_bytes(b"one-file")
    discovery = FileDiscoveryTool().discover(tmp_path, CATALOG)

    validation = InputSetValidationTool().validate(
        discovery.data["files"], CATALOG, expense_month="2026-07", inspect_workbooks=False
    )

    assert not validation.success
    assert "payroll_workbook" in validation.data["missing_required"]
