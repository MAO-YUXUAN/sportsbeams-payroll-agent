from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import shutil
from uuid import uuid4
from typing import Callable

import yaml

from app.approvals import ApprovalService, ApprovalStage
from app.audit import AuditLogger
from app.tools.excel.errors import ExcelOperationError
from app.tools.excel.models import ToolResult
from app.tools.excel.session import ExcelSession
from app.tools.excel.writer import sha256_file
from app.tools.file import FileDiscoveryTool, InputSetValidationTool, RunFilePreparationTool
from app.tools.output import OutputValidationTool, ReconciliationReportTool, VendorPaymentRequestTool
from app.tools.payroll import (
    AttendanceImportTool,
    EmployeeAmountRecord,
    EmployeePayrollUpdateTool,
    FinalPayrollValidationTool,
    HousingFundImportTool,
    MealAllowanceImportTool,
    MealAllowanceGenerateTool,
    NewEmployeePayrollTool,
    PayrollBillImportTool,
    SocialInsuranceImportTool,
    TaxPaymentImportTool,
    PayrollRolloverTool,
    VendorCostImportTool,
    VendorPayrollImportTool,
    VendorPayrollGenerateTool,
    load_vendor_config,
)
from app.tools.payroll.update_department_summary import DepartmentSummaryTool
from app.tools.reconciliation import EmployeeAmountReconciliationTool, VendorSettlementReconciliationTool

from .context import AgentContext
from .state import WorkflowState, ensure_state
from .workflow import evidence_hash


class PayrollAgent:
    """Deterministic Sportsbeams payroll workflow with approval checkpoints."""

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).expanduser().resolve()
        self.runs_root = self.project_root / "data" / "runs"
        self.file_catalog = self.project_root / "knowledge" / "file-catalog.yaml"
        self.vendor_config_dir = self.project_root / "knowledge" / "field-mappings" / "vendors"
        self.default_payment_request_template = (
            self.project_root / "templates" / "payment" / "default-payment-request.xlsx"
        )

    RUNTIME_INPUTS = (
        "yarun_settlement", "yicai_dispatch_settlement",
        "attendance_summary", "social_detail", "tax_payment",
        "housing_fund_detail",
    )
    VENDOR_PAYROLL_INPUTS = (
        "yarun_settlement", "yicai_dispatch_settlement", "attendance_summary",
    )
    INTERNAL_PAYMENT_INPUTS = ("social_detail", "tax_payment", "housing_fund_detail")
    VENDOR_INPUTS = {
        "yarun": "yarun_settlement",
        "yicai": "yicai_dispatch_settlement",
    }
    RETURN_INPUTS = ("payroll_bill", "payroll_export")

    def create_run(self, *, expense_month: str, source_directory: str | Path, run_id: str | None = None) -> AgentContext:
        datetime.strptime(expense_month, "%Y-%m")
        identifier = run_id or f"run-{expense_month.replace('-', '')}-{datetime.now():%Y%m%d%H%M%S}-{uuid4().hex[:6]}"
        run_directory = self.runs_root / expense_month / identifier
        if run_directory.exists():
            raise ExcelOperationError(f"Run already exists: {identifier}")
        source = Path(source_directory).expanduser().resolve()
        if not source.is_dir():
            raise ExcelOperationError(f"Source directory does not exist: {source}")
        context = AgentContext(identifier, expense_month, str(source), str(run_directory))
        context.save()
        self._audit(context).log("run_created", actor="user", status="success", details={"expense_month": expense_month, "source_directory": str(source)})
        return context

    def load(self, run_directory: str | Path) -> AgentContext:
        return AgentContext.load(run_directory)

    def advance(self, context: AgentContext, *, inspect_workbooks: bool = True, progress: Callable[[int, str], None] | None = None) -> AgentContext:
        """Advance until the next human approval boundary or completion."""
        report = progress or (lambda _value, _message: None)
        while True:
            state = context.current_state
            if state == WorkflowState.CREATED:
                report(8, "检查基础工资表和输入文件")
                self.prepare_inputs(context, inspect_workbooks=inspect_workbooks)
            elif state == WorkflowState.INPUTS_PREPARED:
                report(38, "复制月份Sheet并生成本月工资表")
                self.prepare_month_rollover(context)
            elif state == WorkflowState.WAITING_ADDITIONAL_INPUTS:
                report(10, "根据考勤生成餐补表")
                self.generate_available_meal_allowance(context)
                report(10, "根据考勤计算补贴和缺勤扣款")
                self.calculate_available_attendance_adjustments(context)
                report(11, "核对社保、个税和公积金并生成付款单")
                self.generate_available_internal_payment_requests(context)
                report(12, "处理已收到的第三方结算表")
                self.generate_available_payment_requests(context)
                missing = self.missing_runtime_inputs(context)
                context.summaries["required_files"] = self.input_requirements(context)
                context.save()
                if missing:
                    return context
                report(15, "读取结算、考勤和辅助资料")
                adjustments = context.summaries.get("attendance_adjustments", {})
                if not isinstance(adjustments, dict) or adjustments.get("status") != "completed":
                    return context
                self.extract_vendor_costs(context)
            elif state == WorkflowState.WAITING_IMPORT_APPROVAL:
                evidence = context.approval_evidence[ApprovalStage.VENDOR_IMPORT.value]
                if not self._approvals(context).is_approved(ApprovalStage.VENDOR_IMPORT, evidence_hash=evidence):
                    return context
                report(15, "写入并核对主工资表")
                self.update_payroll_and_reconcile(context, progress=report)
            elif state == WorkflowState.WAITING_VENDOR_RETURNS:
                self.generate_available_internal_payment_requests(context)
                missing = self.missing_return_inputs(context)
                context.summaries["required_files"] = self.input_requirements(context)
                context.save()
                if missing:
                    return context
                report(25, "核对易才和亚润回传工资")
                self.reconcile_vendor_returns(context)
            elif state == WorkflowState.WAITING_RECONCILIATION_APPROVAL:
                evidence = context.approval_evidence[ApprovalStage.PAYROLL_RECONCILIATION.value]
                if not self._approvals(context).is_approved(ApprovalStage.PAYROLL_RECONCILIATION, evidence_hash=evidence):
                    return context
                report(25, "生成第三方付款申请单")
                self.generate_payment_requests(context)
            elif state == WorkflowState.WAITING_PAYMENT_APPROVAL:
                evidence = context.approval_evidence[ApprovalStage.PAYMENT_REQUEST.value]
                if not self._approvals(context).is_approved(ApprovalStage.PAYMENT_REQUEST, evidence_hash=evidence):
                    return context
                report(85, "校验并归档所有结果文件")
                self.complete(context)
            else:
                return context

    def prepare_inputs(self, context: AgentContext, *, inspect_workbooks: bool = True) -> AgentContext:
        ensure_state(context.current_state, WorkflowState.CREATED)
        try:
            discovery = FileDiscoveryTool().discover(context.source_directory, self.file_catalog)
            if not discovery.success:
                raise ExcelOperationError("Input discovery found ambiguous files")
            validation = InputSetValidationTool().validate(discovery.data["files"], self.file_catalog, expense_month=context.expense_month, inspect_workbooks=inspect_workbooks)
            if not validation.success:
                raise ExcelOperationError(f"Input validation failed: {validation.errors}")
            prepared = RunFilePreparationTool().prepare(validation, self.runs_root, expense_month=context.expense_month, run_id=context.run_id)
            manifest = __import__("json").loads(Path(prepared.data["manifest_path"]).read_text(encoding="utf-8"))
            files: dict[str, list[str]] = {}
            for item in manifest["files"]:
                files.setdefault(item["category_id"], []).append(item["stored_path"])
            context.files = files
            context.summaries["input_validation"] = {"file_count": prepared.data["file_count"], "unclassified": discovery.data["unclassified"]}
            context.transition(WorkflowState.INPUTS_PREPARED); context.save()
            self._audit(context).log("inputs_prepared", status="success", details={"file_count": prepared.data["file_count"], "categories": sorted(files)})
            return context
        except Exception as error:
            self._fail(context, "inputs_prepare_failed", error); raise

    def extract_vendor_costs(self, context: AgentContext) -> AgentContext:
        ensure_state(context.current_state, WorkflowState.WAITING_ADDITIONAL_INPUTS)
        try:
            extracted = self._extract_vendors(context)
            supporting = self._extract_vendor_payroll_supporting(context)
            summaries = {
                vendor: {
                    "vendor": result.data["vendor"],
                    "expense_month": result.data["expense_month"],
                    "record_count": result.data["record_count"],
                    "component_total": result.data["component_total"],
                    "row_total": result.data["row_total"],
                    "reported_total": result.data["reported_total"],
                }
                for vendor, result in extracted.items()
            }
            supporting_summary = self._supporting_summary(supporting)
            context.summaries["vendor_import"] = summaries
            context.summaries["supporting_inputs"] = supporting_summary
            evidence = self._vendor_import_evidence(context, summaries, supporting_summary)
            context.approval_evidence[ApprovalStage.VENDOR_IMPORT.value] = evidence
            approval = self._approvals(context).create(run_id=context.run_id, expense_month=context.expense_month, stage=ApprovalStage.VENDOR_IMPORT, summary="确认亚润和易才社保公积金结算数据", evidence_hash=evidence, details={vendor: {"record_count": value["record_count"], "total": value["component_total"]} for vendor, value in summaries.items()})
            context.summaries["pending_approval_id"] = approval.id
            context.transition(WorkflowState.WAITING_IMPORT_APPROVAL); context.save()
            self._audit(context).log("vendor_costs_extracted", status="pending", details={
                "approval_id": approval.id,
                "vendors": {key: value["record_count"] for key, value in summaries.items()},
            })
            return context
        except Exception as error:
            self._fail(context, "vendor_extraction_failed", error); raise

    def prepare_month_rollover(self, context: AgentContext) -> AgentContext:
        """Ensure the target month sheet exists, rolling the prior month when needed."""
        ensure_state(context.current_state, WorkflowState.INPUTS_PREPARED)
        source = self._one_file(context, "payroll_workbook")
        month = int(context.expense_month.split("-")[1])
        target_sheet = f"{month}月"
        previous_month = 12 if month == 1 else month - 1
        source_sheet = f"{previous_month}月"
        previous_previous = 12 if previous_month == 1 else previous_month - 1
        with ExcelSession(source, read_only=True) as session:
            names = [str(session.workbook.Worksheets(i).Name) for i in range(1, session.workbook.Worksheets.Count + 1)]
        if target_sheet not in names:
            output = Path(context.run_directory) / "output" / "payroll" / f"滚动更新后工资表-{context.expense_month.replace('-', '')}.xlsx"
            replacements = [
                {"old": f"'{previous_previous}月'!", "new": f"'{previous_month}月'!", "reason": "Roll cumulative formulas to prior month"},
                {"old": f"{previous_previous}月!", "new": f"'{previous_month}月'!", "reason": "Roll cumulative formulas to prior month"},
            ]
            result = PayrollRolloverTool().rollover_payroll_month(
                source, output, source_sheet, target_sheet, "A1",
                f"{context.expense_month.split('-')[0]}年{month}月份工资---赛倍明",
                replacements, dry_run=False,
                copy_position="after",
            )
            context.files["payroll_workbook"] = [str(output)]
            context.outputs["rolled_payroll"] = str(output)
            context.summaries["rollover"] = {
                "performed": True, "source_sheet": source_sheet, "target_sheet": target_sheet,
                "formula_change_count": len(result.data["formula_changes"]),
            }
            self._audit(context).log("payroll_month_rolled", status="success", details=context.summaries["rollover"])
        else:
            source_path = Path(source)
            published = Path(context.run_directory) / "output" / "payroll" / f"已含本月Sheet的工资表-{context.expense_month.replace('-', '')}{source_path.suffix.lower()}"
            published.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, published)
            context.outputs["rolled_payroll"] = str(published)
            context.summaries["rollover"] = {
                "performed": False, "reason": "target_sheet_already_exists", "target_sheet": target_sheet,
                "published_existing_workbook": str(published),
            }
            self._audit(context).log("payroll_month_already_present", status="success", details=context.summaries["rollover"])
        context.summaries["required_files"] = self.input_requirements(context)
        context.transition(WorkflowState.WAITING_ADDITIONAL_INPUTS)
        context.save()
        return context

    def missing_runtime_inputs(self, context: AgentContext) -> list[str]:
        return [
            category for category in self.VENDOR_PAYROLL_INPUTS
            if len(context.files.get(category, [])) != 1
        ]

    def missing_return_inputs(self, context: AgentContext) -> list[str]:
        return [
            category for category in (*self.RETURN_INPUTS, *self.INTERNAL_PAYMENT_INPUTS)
            if len(context.files.get(category, [])) != 1
        ]

    def generate_available_payment_requests(self, context: AgentContext) -> dict[str, str]:
        """Generate one payment request per received settlement without waiting for the other vendor."""
        ensure_state(context.current_state, WorkflowState.WAITING_ADDITIONAL_INPUTS)
        template, template_source = self._payment_request_template(context)
        generated: dict[str, str] = {}
        amounts = dict(context.summaries.get("payment_amounts", {}))
        reconciliations = dict(context.summaries.get("available_vendor_reconciliation", {}))
        today = date.today().isoformat()
        payroll_path = Path(
            context.outputs.get("rolled_payroll")
            or self._one_file(context, "payroll_workbook")
        )
        summary_tool = DepartmentSummaryTool()
        for vendor, category in self.VENDOR_INPUTS.items():
            paths = context.files.get(category, [])
            if len(paths) != 1:
                continue
            output_key = f"payment_{vendor}"
            existing = Path(context.outputs.get(output_key, "")) if context.outputs.get(output_key) else None
            if existing and existing.is_file():
                continue
            cfg = load_vendor_config(self.vendor_config_dir / f"{vendor}.yaml", context.expense_month)
            extracted = VendorCostImportTool().extract(
                Path(paths[0]),
                vendor=cfg.vendor_name,
                expense_month=context.expense_month,
                mapping=cfg.settlement,
            )
            records = extracted.data["record_objects"]
            payroll_records = summary_tool.read_summary(
                payroll_path,
                records,
                cfg.payroll_summary,
            )
            reconciliation = VendorSettlementReconciliationTool().reconcile(
                records,
                payroll_records,
                fields=cfg.reconciliation_fields,
            )
            reconciliations[vendor] = reconciliation.data
            if not reconciliation.success:
                self._audit(context).log(
                    "vendor_payment_request_blocked",
                    status="blocked",
                    details={
                        "vendor": vendor,
                        "reason": "settlement_does_not_match_payroll",
                        "reconciliation": reconciliation.data,
                    },
                )
                continue
            amount = sum(
                (record.amount(field) for record in records for field in cfg.reconciliation_fields),
                Decimal("0.00"),
            )
            output = (
                Path(context.run_directory)
                / "output"
                / "payment_requests"
                / f"{vendor}-payment-{context.expense_month.replace('-', '')}.xlsx"
            )
            result = VendorPaymentRequestTool().generate(
                template,
                output,
                sheet_name=cfg.payment_sheet_name,
                cell_mapping=cfg.payment_cells,
                values={
                    "payment_date": today,
                    "vendor_name": cfg.vendor_name,
                    "payment_amount": amount,
                    "purpose": cfg.payment_purpose,
                },
                reconciliation_passed=True,
                dry_run=False,
            )
            context.outputs[output_key] = result.data["output_path"]
            amounts[vendor] = str(amount)
            generated[vendor] = result.data["output_path"]
            self._audit(context).log(
                "vendor_payment_request_generated",
                status="success",
                details={
                    "vendor": vendor,
                    "amount": str(amount),
                    "source_category": category,
                    "template_source": template_source,
                },
            )
        if reconciliations:
            context.summaries["available_vendor_reconciliation"] = reconciliations
        if generated:
            context.summaries["payment_amounts"] = amounts
            context.summaries["payment_request_template"] = {
                "path": str(template),
                "source": template_source,
            }
        if generated or reconciliations:
            context.save()
        return generated

    def generate_available_meal_allowance(self, context: AgentContext) -> str | None:
        """Generate the meal workbook as soon as attendance is available."""
        attendance_paths = context.files.get("attendance_summary", [])
        if len(attendance_paths) != 1:
            return None
        existing = Path(context.outputs.get("meal_allowance", "")) if context.outputs.get("meal_allowance") else None
        if existing and existing.is_file():
            return str(existing)
        attendance = AttendanceImportTool().extract(
            Path(attendance_paths[0]),
            expense_month=context.expense_month,
        )
        config = yaml.safe_load(
            (self.project_root / "knowledge" / "field-mappings" / "payroll-inputs.yaml").read_text(
                encoding="utf-8"
            )
        )["meal_allowance"]
        output = (
            Path(context.run_directory)
            / "output"
            / "payroll"
            / f"14.{context.expense_month[:4]}年餐费-赛倍明.xlsx"
        )
        generated = MealAllowanceGenerateTool().generate(
            attendance.data["record_objects"],
            output,
            expense_month=context.expense_month,
            employees=config["employees"],
            daily_rate=int(config["daily_rate"]),
            deductible_statuses=list(config["deductible_statuses"]),
        )
        context.outputs["meal_allowance"] = str(output)
        context.summaries["meal_generation"] = generated.data
        context.save()
        self._audit(context).log(
            "meal_allowance_generated",
            status="success",
            details={
                "path": str(output),
                "record_count": generated.data["record_count"],
                "total": generated.data["total"],
            },
        )
        return str(output)

    def calculate_available_attendance_adjustments(self, context: AgentContext) -> dict | None:
        """Calculate attendance additions/deductions, leaving payroll writes for review."""
        attendance_paths = context.files.get("attendance_summary", [])
        payroll_value = context.outputs.get("rolled_payroll")
        if len(attendance_paths) != 1 or not payroll_value:
            return None
        attendance = AttendanceImportTool().extract(
            Path(attendance_paths[0]), expense_month=context.expense_month
        )
        config = yaml.safe_load(
            (self.project_root / "knowledge" / "field-mappings" / "payroll-inputs.yaml").read_text(
                encoding="utf-8"
            )
        )["meal_allowance"]
        meal_employees = {
            str(item["employee_name"]).strip() for item in config["employees"]
        }
        daily_rate = Decimal(str(config["daily_rate"]))
        deductible_meal_statuses = set(config["deductible_statuses"])
        payroll_path = Path(payroll_value)
        month_sheet = f"{int(context.expense_month.split('-')[1])}月"
        payroll_rows: dict[str, tuple[int, Decimal, str]] = {}
        current_adjustment_values: dict[str, tuple[Decimal, Decimal]] = {}
        with ExcelSession(payroll_path, read_only=True) as session:
            sheet = session.sheet(month_sheet)
            for row in range(8, 39):
                name = str(sheet.Range(f"C{row}").Text).strip()
                if name:
                    payroll_rows[name] = (row, Decimal(str(sheet.Range(f"H{row}").Value2 or 0)), name)
                    current_adjustment_values[name] = (
                        Decimal(str(sheet.Range(f"K{row}").Value2 or 0)).quantize(Decimal("0.01")),
                        Decimal(str(sheet.Range(f"M{row}").Value2 or 0)).quantize(Decimal("0.01")),
                    )
                    normalized_name = name.replace(" ", "").rstrip("1１")
                    payroll_rows.setdefault(normalized_name, payroll_rows[name])
                    current_adjustment_values.setdefault(
                        normalized_name, current_adjustment_values[name]
                    )

        details = []
        unmatched = []
        for record in attendance.data["record_objects"]:
            source_name = record.employee_name.strip()
            payroll_name = source_name
            payroll = payroll_rows.get(source_name) or payroll_rows.get(
                source_name.replace(" ", "").rstrip("1１")
            )
            if payroll is None:
                unmatched.append(source_name)
                continue
            row, salary, payroll_name = payroll
            workdays = Decimal(str(record.scheduled_workdays))
            business_trip_days = Decimal(str(record.status_counts.get("出差", 0)))
            personal_leave_days = Decimal(str(record.status_counts.get("事假", 0)))
            absence_days = Decimal(str(record.status_counts.get("旷工", 0)))
            leave_days = personal_leave_days + absence_days
            attendance_days = max(Decimal("0"), workdays - leave_days)
            if attendance_days < Decimal("11"):
                deduction = salary / Decimal("21.75") * (Decimal("21.75") - attendance_days)
            else:
                deduction = salary / Decimal("21.75") * leave_days
            deduction = deduction.quantize(Decimal("0.01"))
            meal_deduction_days = Decimal(str(sum(
                count for status, count in record.status_counts.items()
                if status in deductible_meal_statuses
            )))
            is_meal_employee = source_name in meal_employees
            bonus = Decimal("0") if is_meal_employee else (
                Decimal("100") + max(Decimal("0"), workdays - business_trip_days) * daily_rate
            )
            details.append({
                "employee_name": source_name,
                "payroll_name": payroll_name,
                "payroll_row": row,
                "scheduled_workdays": int(workdays),
                "business_trip_days": int(business_trip_days),
                "personal_leave_days": int(personal_leave_days),
                "absence_days": int(absence_days),
                "attendance_days": str(attendance_days),
                "salary": str(salary),
                "group": "meal_table" if is_meal_employee else "payroll_bonus",
                "transport_allowance": "0" if is_meal_employee else "100",
                "meal_deduction_days": int(meal_deduction_days),
                "meal_deduction": str((meal_deduction_days * daily_rate).quantize(Decimal("0.01"))),
                "bonus": str(bonus.quantize(Decimal("0.01"))),
                "absence_deduction": str(deduction),
                "target_bonus_cell": f"K{row}",
                "target_absence_cell": f"M{row}",
            })
        employee_requests = self._build_new_employee_requests(context, unmatched)
        input_signature = evidence_hash({
            "rule": "attendance_allowance_v2",
            "details": details,
            "unmatched": unmatched,
        })
        previous = context.summaries.get("attendance_adjustments", {})
        if (
            isinstance(previous, dict)
            and previous.get("status") == "completed"
            and previous.get("input_signature") == input_signature
        ):
            return previous
        applied_events = self._audit(context).read(
            event_type="attendance_adjustments_applied", status="success"
        )
        values_still_applied = bool(applied_events) and not unmatched and all(
            current_adjustment_values.get(item["payroll_name"]) == (
                Decimal(str(item["bonus"])).quantize(Decimal("0.01")),
                Decimal(str(item["absence_deduction"])).quantize(Decimal("0.01")),
            )
            for item in details
        )
        result = {
            "status": "completed" if values_still_applied else "waiting_review",
            "requires_confirmation": not values_still_applied,
            "rule": "attendance_allowance_v2",
            "input_signature": input_signature,
            "daily_meal_rate": str(daily_rate),
            "transport_allowance": "100",
            "salary_divisor": "21.75",
            "details": details,
            "unmatched": unmatched,
            "suspected_new_employees": employee_requests,
        }
        if values_still_applied:
            last_applied = applied_events[-1]
            result["applied_by"] = last_applied.actor
            result["apply_result"] = dict(last_applied.details)
        if context.summaries.get("attendance_adjustments") == result:
            return result
        context.summaries["attendance_adjustments"] = result
        context.save()
        if not values_still_applied:
            self._audit(context).log(
                "attendance_adjustments_calculated",
                status="pending" if unmatched else "success",
                details={
                    "record_count": len(details),
                    "unmatched": unmatched,
                    "requires_confirmation": True,
                    "input_signature": input_signature,
                },
            )
        return result

    def _build_new_employee_requests(self, context: AgentContext, unmatched: list[str]) -> list[dict]:
        """Turn attendance-only names into explicit, non-destructive onboarding requests.

        A name is treated as a suspected new employee when it is also present in a
        received vendor settlement.  The employee is not written to payroll until
        the required salary facts have been supplied and explicitly confirmed.
        """
        if not unmatched:
            return []
        vendor_by_name: dict[str, str] = {}
        for vendor, category in self.VENDOR_INPUTS.items():
            paths = context.files.get(category, [])
            if len(paths) != 1:
                continue
            try:
                config = load_vendor_config(
                    self.vendor_config_dir / f"{vendor}.yaml", context.expense_month
                )
                extracted = VendorCostImportTool().extract(
                    Path(paths[0]), vendor=config.vendor_name, expense_month=context.expense_month,
                    mapping=config.settlement,
                )
            except Exception:
                continue
            for record in extracted.data.get("records", []):
                name = str(record.get("employee_name", "")).strip()
                if name:
                    vendor_by_name[name.replace(" ", "").rstrip("1１")] = vendor

        existing = {
            str(item.get("employee_name")): item
            for item in context.summaries.get("new_employee_requests", [])
            if isinstance(item, dict)
        }
        requests: list[dict] = []
        for name in unmatched:
            normalized = name.replace(" ", "").rstrip("1１")
            vendor = vendor_by_name.get(normalized)
            if not vendor:
                continue
            previous = existing.get(name, {})
            request = {
                "id": previous.get("id") or f"new_employee_{context.expense_month}_{name}",
                "employee_name": name,
                "expense_month": context.expense_month,
                "status": previous.get("status", "awaiting_information"),
                "detected_from": ["attendance_summary", self.VENDOR_INPUTS[vendor]],
                "suggested_employment_entity": vendor,
                "answers": previous.get("answers", {}),
                "required_fields": [
                    {"key": "department", "label": "所属部门"},
                    {"key": "employment_entity", "label": "用工/结算主体"},
                    {"key": "hire_date", "label": "入职日期"},
                    {"key": "base_salary", "label": "月基础工资"},
                    {"key": "payroll_method", "label": "首月计薪方式"},
                ],
            }
            requests.append(request)
        if requests:
            context.summaries["new_employee_requests"] = requests
            notifications = context.summaries.setdefault("agent_notifications", [])
            known_ids = {item.get("id") for item in notifications if isinstance(item, dict)}
            for request in requests:
                notification_id = f"ask_{request['id']}"
                if notification_id in known_ids or request["status"] != "awaiting_information":
                    continue
                vendor_label = {"yarun": "亚润", "yicai": "易才"}.get(
                    request["suggested_employment_entity"], request["suggested_employment_entity"]
                )
                notifications.append({
                    "id": notification_id,
                    "type": "new_employee_information_request",
                    "status": "unread",
                    "employee_name": request["employee_name"],
                    "message": (
                        f"我在本月考勤表和{vendor_label}结算表中发现 {request['employee_name']}，"
                        "但上月工资表没有该员工，已将其列为疑似新入职员工。\n\n"
                        "正式新增到本月工资表前，请提供：\n"
                        "1. 所属部门\n2. 用工/结算主体（公司、亚润或易才）\n"
                        "3. 入职日期\n4. 月基础工资\n5. 首月按整月还是按入职日折算\n\n"
                        "可以直接回复，例如：刘同涛，销售部，亚润，2026-07-01，基础工资8000，按整月计薪。"
                    ),
                })
        return requests

    def confirm_new_employee(self, context: AgentContext, employee_name: str, *, actor: str) -> dict:
        requests = context.summaries.get("new_employee_requests", [])
        request = next(
            (item for item in requests if isinstance(item, dict)
             and item.get("employee_name") == employee_name),
            None,
        )
        if request is None:
            raise ExcelOperationError(f"No new employee request exists for: {employee_name}")
        if request.get("status") == "completed":
            return dict(request.get("result", {}))
        if request.get("status") != "ready_for_preview":
            raise ExcelOperationError(f"New employee information is incomplete for: {employee_name}")
        answers = request.get("answers", {})
        required = {field["key"] for field in request.get("required_fields", [])}
        if required.difference(answers):
            raise ExcelOperationError(f"New employee information is incomplete for: {employee_name}")
        if answers.get("payroll_method") != "full_month":
            raise ExcelOperationError("Prorated first-month payroll requires a confirmed payable salary amount")

        payroll_path = Path(context.outputs.get("rolled_payroll", ""))
        backup_path = (
            Path(context.run_directory) / "backup" /
            f"before-new-employee-{employee_name}-{context.expense_month.replace('-', '')}.xlsx"
        )
        month_sheet = f"{int(context.expense_month.split('-')[1])}月"
        result = NewEmployeePayrollTool().add(
            payroll_path,
            sheet_name=month_sheet,
            employee_name=employee_name,
            department=str(answers["department"]),
            base_salary=Decimal(str(answers["base_salary"])),
            backup_path=backup_path,
        ).data
        request["status"] = "completed"
        request["confirmed_by"] = actor
        request["result"] = result
        context.summaries["new_employee_requests"] = requests
        context.save()
        self._audit(context).log(
            "new_employee_added_to_payroll", actor=actor, status="success",
            details={
                "employee_name": employee_name,
                "department": answers["department"],
                "employment_entity": answers["employment_entity"],
                "hire_date": answers["hire_date"],
                "base_salary": answers["base_salary"],
                "payroll_method": answers["payroll_method"],
                "row": result.get("row"),
                "backup_path": str(backup_path),
            },
        )
        self.calculate_available_attendance_adjustments(context)
        return result

    @staticmethod
    def sync_agent_notifications(context: AgentContext) -> bool:
        """Create any follow-up Agent messages implied by persisted workflow data."""
        notifications = context.summaries.setdefault("agent_notifications", [])
        known_ids = {item.get("id") for item in notifications if isinstance(item, dict)}
        changed = False
        for request in context.summaries.get("new_employee_requests", []):
            if isinstance(request, dict) and request.get("status") == "completed":
                request_id = str(request.get("id", ""))
                for notification in notifications:
                    if (
                        isinstance(notification, dict)
                        and notification.get("id") in {f"ask_{request_id}", f"preview_{request_id}"}
                        and notification.get("status") != "acknowledged"
                    ):
                        notification["status"] = "acknowledged"
                        changed = True
                continue
            if not isinstance(request, dict) or request.get("status") != "ready_for_preview":
                continue
            notification_id = f"preview_{request.get('id')}"
            if notification_id in known_ids:
                continue
            answers = request.get("answers", {})
            method = "整月计薪" if answers.get("payroll_method") == "full_month" else "按入职日折算"
            name = str(request.get("employee_name", ""))
            notifications.append({
                "id": notification_id,
                "type": "new_employee_preview_ready",
                "status": "unread",
                "employee_name": name,
                "message": (
                    f"{name} 的新增预览已准备好：\n"
                    f"- 部门：{answers.get('department')}\n"
                    f"- 主体：{answers.get('employment_entity')}\n"
                    f"- 入职日期：{answers.get('hire_date')}\n"
                    f"- 月基础工资：{answers.get('base_salary')} 元\n"
                    f"- 首月计薪：{method}\n\n"
                    f"确认无误请回复：确认新增{name}"
                ),
            })
            known_ids.add(notification_id)
            changed = True
        adjustments = context.summaries.get("attendance_adjustments", {})
        if isinstance(adjustments, dict) and adjustments.get("status") == "completed":
            for notification in notifications:
                if (
                    isinstance(notification, dict)
                    and notification.get("type") == "attendance_adjustments_review"
                    and notification.get("status") != "acknowledged"
                ):
                    notification["status"] = "acknowledged"
                    changed = True
        if isinstance(adjustments, dict) and adjustments.get("status") == "waiting_review":
            notification_id = f"review_attendance_adjustments_{context.expense_month}"
            if notification_id not in known_ids:
                details = adjustments.get("details", [])
                total_bonus = sum(Decimal(str(item.get("bonus", "0"))) for item in details)
                total_deduction = sum(Decimal(str(item.get("absence_deduction", "0"))) for item in details)
                notifications.append({
                    "id": notification_id,
                    "type": "attendance_adjustments_review",
                    "status": "unread",
                    "message": (
                        f"补扣款计算已完成，共 {len(details)} 人。\n"
                        f"- 奖金/补贴合计：{total_bonus.quantize(Decimal('0.01'))} 元\n"
                        f"- 缺勤扣款合计：{total_deduction.quantize(Decimal('0.01'))} 元\n\n"
                        "确认无误请回复：确认写入补扣款"
                    ),
                })
                known_ids.add(notification_id)
                changed = True
        if changed:
            context.save()
        return changed

    def apply_attendance_adjustments(self, context: AgentContext, *, actor: str) -> dict:
        summary = context.summaries.get("attendance_adjustments", {})
        if not isinstance(summary, dict) or summary.get("status") not in {"waiting_review", "completed"}:
            raise ExcelOperationError("No attendance adjustment preview is ready for confirmation")
        if summary.get("status") == "completed":
            return dict(summary.get("apply_result", {}))
        details = summary.get("details", [])
        if not details:
            raise ExcelOperationError("Attendance adjustment preview is empty")
        if summary.get("unmatched"):
            raise ExcelOperationError("Attendance adjustments still contain unmatched employees")

        payroll_path = Path(context.outputs.get("rolled_payroll", ""))
        backup_path = (
            Path(context.run_directory) / "backup" /
            f"before-attendance-adjustments-{context.expense_month.replace('-', '')}.xlsx"
        )
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        if not backup_path.exists():
            shutil.copy2(payroll_path, backup_path)
        month_sheet = f"{int(context.expense_month.split('-')[1])}月"
        with ExcelSession(payroll_path, read_only=False) as session:
            sheet = session.sheet(month_sheet)
            for item in details:
                row = int(item["payroll_row"])
                if str(sheet.Range(f"C{row}").Text).strip() != str(item["payroll_name"]).strip():
                    raise ExcelOperationError(
                        f"Payroll row changed before adjustment write: {item['employee_name']}"
                    )
                sheet.Range(str(item["target_bonus_cell"])).Value2 = float(Decimal(str(item["bonus"])))
                sheet.Range(str(item["target_absence_cell"])).Value2 = float(
                    Decimal(str(item["absence_deduction"]))
                )
            session.app.CalculateFullRebuild()
            session.workbook.Save()

        apply_result = {
            "status": "completed",
            "record_count": len(details),
            "total_bonus": str(sum(Decimal(str(item["bonus"])) for item in details).quantize(Decimal("0.01"))),
            "total_absence_deduction": str(sum(
                Decimal(str(item["absence_deduction"])) for item in details
            ).quantize(Decimal("0.01"))),
            "backup_path": str(backup_path),
        }
        summary["status"] = "completed"
        summary["requires_confirmation"] = False
        summary["applied_by"] = actor
        summary["apply_result"] = apply_result
        for notification in context.summaries.get("agent_notifications", []):
            if isinstance(notification, dict) and notification.get("type") == "attendance_adjustments_review":
                notification["status"] = "acknowledged"
        context.summaries["attendance_adjustments"] = summary
        context.save()
        self._audit(context).log(
            "attendance_adjustments_applied", actor=actor, status="success", details=apply_result
        )
        return apply_result

    def generate_available_internal_payment_requests(self, context: AgentContext) -> dict[str, str]:
        """Independently reconcile and generate payment requests for files 19, 20 and 21."""
        payroll_path = Path(
            context.outputs.get("rolled_payroll")
            or self._one_file(context, "payroll_workbook")
        )
        month_sheet = f"{int(context.expense_month.split('-')[1])}月"
        year, month = (int(part) for part in context.expense_month.split("-", 1))
        today = date.today().isoformat()
        template_root = self.project_root / "templates" / "internal-payments"
        generated: dict[str, str] = {}
        reconciliations = dict(context.summaries.get("internal_payment_reconciliation", {}))

        definitions = {
            "social": {
                "category": "social_detail",
                "output_key": "payment_internal_social",
                "template": template_root / "default-social-payment-request.xlsx",
                "output_name": f"19.社保付款申请单-{context.expense_month.replace('-', '')}.xlsx",
                "vendor_name": "上海赛倍明照明科技有限公司",
                "purpose": f"支付赛倍明{year}年{month}月社保费用",
                "mapping": {
                    "payment_date": "C4", "vendor_name": "C6",
                    "payment_amount": ["C8", "D14"], "amount_uppercase": ["C9", "D15"],
                    "purpose": "C13",
                },
            },
            "tax": {
                "category": "tax_payment",
                "output_key": "payment_internal_tax",
                "template": template_root / "default-tax-payment-request.xlsx",
                "output_name": f"20.个税付款申请单-{context.expense_month.replace('-', '')}.xlsx",
                "vendor_name": "上海赛倍明照明科技有限公司",
                "purpose": f"支付赛倍明{year}年{month}月份个人所得税费用",
                "mapping": {
                    "payment_date": "C5", "vendor_name": "C7",
                    "payment_amount": ["C9", "D15"], "amount_uppercase": ["C10", "D16"],
                    "purpose": "C14", "company_name": "K8", "employee_count": "L8",
                    "declared_tax": "M8", "payroll_tax": "N8", "difference": "O8",
                },
            },
            "housing": {
                "category": "housing_fund_detail",
                "output_key": "payment_internal_housing",
                "template": template_root / "default-housing-payment-request.xlsx",
                "output_name": f"21.公积金付款申请单-{context.expense_month.replace('-', '')}.xlsx",
                "vendor_name": "上海市公积金管理中心（房改资金）",
                "purpose": f"支付赛倍明{year}年{month}月份公积金费用",
                "mapping": {
                    "payment_date": "C4", "vendor_name": "C6",
                    "payment_amount": ["C8", "D14"], "amount_uppercase": ["C9", "D15"],
                    "purpose": "C13",
                },
            },
        }

        for payment_type, definition in definitions.items():
            paths = context.files.get(definition["category"], [])
            if len(paths) != 1:
                continue
            existing_path = context.outputs.get(definition["output_key"])
            if existing_path and Path(existing_path).is_file():
                continue
            template = Path(definition["template"])
            if not template.is_file():
                raise ExcelOperationError(f"Default internal payment template does not exist: {template}")

            values = {
                "payment_date": today,
                "vendor_name": definition["vendor_name"],
                "purpose": definition["purpose"],
            }
            if payment_type == "social":
                imported = SocialInsuranceImportTool().extract(paths[0], expense_month=context.expense_month)
                records = imported.data["record_objects"]
                fields = [
                    "pension_company", "medical_company", "unemployment_company",
                    "injury_company", "supplemental_medical_company", "pension_employee",
                    "medical_employee", "unemployment_employee",
                ]
                update = self._update_internal_payroll_values(
                    payroll_path,
                    context,
                    records,
                    payment_type="social",
                    field_columns={
                        "social_base": "Q", "pension_company": "R", "medical_company": "S",
                        "unemployment_company": "T", "injury_company": "U",
                        "supplemental_medical_company": "V", "pension_employee": "AA",
                        "medical_employee": "AB", "unemployment_employee": "AC",
                    },
                )
                if update.success:
                    payroll_records = self._read_internal_payroll(
                        payroll_path, context.expense_month, {record.employee_name for record in records}
                    )
                    check = EmployeeAmountReconciliationTool().reconcile(records, payroll_records, fields=fields)
                else:
                    check = update
                amount = Decimal(imported.data["total"])
            elif payment_type == "housing":
                imported = HousingFundImportTool().extract(paths[0], expense_month=context.expense_month)
                records = imported.data["record_objects"]
                update = self._update_internal_payroll_values(
                    payroll_path,
                    context,
                    records,
                    payment_type="housing",
                    field_columns={
                        "housing_base": "W", "housing_company": "X", "housing_employee": "AD",
                    },
                )
                if update.success:
                    payroll_records = self._read_internal_payroll(
                        payroll_path, context.expense_month, {record.employee_name for record in records}
                    )
                    check = EmployeeAmountReconciliationTool().reconcile(
                        records, payroll_records, fields=["housing_company", "housing_employee"]
                    )
                else:
                    check = update
                amount = Decimal(imported.data["total"])
            else:
                imported = TaxPaymentImportTool().extract(paths[0], expense_month=context.expense_month)
                with ExcelSession(payroll_path, read_only=True) as session:
                    payroll_tax = Decimal(
                        str(session.sheet(month_sheet).Range("AI45").Text).replace(",", "") or "0"
                    ).quantize(Decimal("0.01"))
                declared_tax = Decimal(imported.data["declared_tax"]).quantize(Decimal("0.01"))
                tax_passed = imported.success and declared_tax == payroll_tax
                check = ToolResult(
                    tax_passed,
                    "reconcile_internal_tax",
                    {
                        "passed": tax_passed,
                        "declared_tax": str(declared_tax),
                        "payroll_tax": str(payroll_tax),
                        "difference": str(declared_tax - payroll_tax),
                    },
                )
                amount = Decimal(imported.data["payment_amount"])
                values.update({
                    "company_name": "赛倍明",
                    "employee_count": imported.data["employee_count"],
                    "declared_tax": declared_tax,
                    "payroll_tax": payroll_tax,
                    "difference": declared_tax - payroll_tax,
                })

            reconciliations[payment_type] = check.data
            if not check.success:
                self._audit(context).log(
                    "internal_payment_request_blocked",
                    status="blocked",
                    details={"payment_type": payment_type, "reconciliation": check.data},
                )
                continue
            values["payment_amount"] = amount
            output = Path(context.run_directory) / "output" / "payment_requests" / definition["output_name"]
            result = VendorPaymentRequestTool().generate(
                template,
                output,
                sheet_name="付款单",
                cell_mapping=definition["mapping"],
                values=values,
                reconciliation_passed=True,
                dry_run=False,
            )
            context.outputs[definition["output_key"]] = result.data["output_path"]
            generated[payment_type] = result.data["output_path"]
            self._audit(context).log(
                "internal_payment_request_generated",
                status="success",
                details={
                    "payment_type": payment_type,
                    "amount": str(amount),
                    "template_source": "default",
                    "source_category": definition["category"],
                },
            )

        if reconciliations:
            context.summaries["internal_payment_reconciliation"] = reconciliations
        if generated or reconciliations:
            context.save()
        return generated

    def _update_internal_payroll_values(
        self,
        payroll_path: Path,
        context: AgentContext,
        records: list[EmployeeAmountRecord],
        *,
        payment_type: str,
        field_columns: dict[str, str],
    ) -> ToolResult[dict]:
        """Validate employee matching, then import authoritative monthly benefit values."""
        sheet_name = f"{int(context.expense_month.split('-')[1])}月"
        tool = EmployeePayrollUpdateTool()
        common = {
            "sheet_name": sheet_name,
            "start_row": 8,
            "end_row": 38,
            "employee_name_column": "C",
            "field_columns": field_columns,
            "allow_formula_overwrite": True,
        }
        preview = tool.update(payroll_path, records, dry_run=True, **common)
        if not preview.success:
            return ToolResult(
                False,
                "update_internal_payroll_values",
                {
                    "status": "blocked",
                    "payment_type": payment_type,
                    "reason": "employee_matching_failed",
                    "unmatched": preview.data.get("unmatched", []),
                    "duplicates": preview.data.get("duplicates", []),
                },
            )
        applied = tool.update(
            payroll_path,
            records,
            output_path=payroll_path,
            dry_run=False,
            **common,
        )
        self._audit(context).log(
            "internal_payroll_values_updated",
            status="success",
            details={
                "payment_type": payment_type,
                "change_count": len(applied.data.get("changes", [])),
                "fields": sorted(field_columns),
                "payroll_sha256": sha256_file(payroll_path),
            },
        )
        return applied

    def repair_current_run(self, context: AgentContext) -> dict:
        """Repair only the current run's published payroll copy; never mutate uploaded source files."""
        payroll_source_dir = Path(context.run_directory) / "input" / "source" / "payroll"
        source_candidates = [
            path for path in payroll_source_dir.iterdir()
            if path.is_file() and path.suffix.lower() in {".xls", ".xlsx", ".xlsm", ".xlsb"}
        ] if payroll_source_dir.is_dir() else []
        if len(source_candidates) != 1:
            raise ExcelOperationError(
                f"Expected exactly one original payroll workbook; found {len(source_candidates)}"
            )
        output_value = context.outputs.get("rolled_payroll")
        if not output_value or not Path(output_value).is_file():
            raise ExcelOperationError("The current run does not have a published payroll workbook to repair")

        month = int(context.expense_month.split("-")[1])
        previous_month = 12 if month == 1 else month - 1
        output = Path(output_value)
        before_hash = sha256_file(output)
        result = PayrollRolloverTool().repair_workbook_structure(
            source_candidates[0],
            output,
            source_sheet=f"{previous_month}月",
            target_sheet=f"{month}月",
        )
        details = {
            "status": result.data["status"],
            "restored_sheets": result.data["restored_sheets"],
            "before_order": result.data["before_order"],
            "after_order": result.data["after_order"],
            "before_sha256": before_hash,
            "after_sha256": result.data["output_sha256"],
        }
        context.summaries["workbook_repair"] = details
        if context.last_error and "Worksheet does not exist" in context.last_error:
            context.last_error = None
        self._audit(context).log(
            "workbook_structure_repaired",
            actor="agent",
            status="success",
            details=details,
        )
        context.save()
        return details

    def input_requirements(self, context: AgentContext) -> list[dict[str, str | bool]]:
        categories = (
            (*self.RETURN_INPUTS, *self.INTERNAL_PAYMENT_INPUTS)
            if context.current_state == WorkflowState.WAITING_VENDOR_RETURNS
            else self.RUNTIME_INPUTS
        )
        return [
            {"category": category, "received": len(context.files.get(category, [])) == 1}
            for category in categories
        ]

    def update_payroll_and_reconcile(self, context: AgentContext, *, progress: Callable[[int, str], None] | None = None) -> AgentContext:
        ensure_state(context.current_state, WorkflowState.WAITING_IMPORT_APPROVAL)
        evidence = context.approval_evidence[ApprovalStage.VENDOR_IMPORT.value]
        if not self._approvals(context).is_approved(ApprovalStage.VENDOR_IMPORT, evidence_hash=evidence):
            raise ExcelOperationError("Vendor import approval has not been granted")
        try:
            report = progress or (lambda _value, _message: None)
            current_evidence = self._vendor_import_evidence(
                context,
                context.summaries["vendor_import"],
                context.summaries["supporting_inputs"],
            )
            if current_evidence != evidence:
                raise ExcelOperationError("Vendor source data changed after approval")
            extracted = self._extract_vendors(context)
            supporting_for_evidence = self._extract_vendor_payroll_supporting(context)
            payroll_source = self._one_file(context, "payroll_workbook")
            working = Path(context.run_directory) / "working" / "payroll-after-yarun.xlsx"
            final = Path(context.run_directory) / "output" / "payroll" / f"sportsbeams-payroll-{context.expense_month.replace('-', '')}.xlsx"
            summary_tool = DepartmentSummaryTool()
            yarun_cfg = load_vendor_config(self.vendor_config_dir / "yarun.yaml", context.expense_month)
            yicai_cfg = load_vendor_config(self.vendor_config_dir / "yicai.yaml", context.expense_month)
            summary_tool.update(payroll_source, extracted["yarun"].data["record_objects"], yarun_cfg.payroll_summary, output_path=working, dry_run=False)
            summary_tool.update(working, extracted["yicai"].data["record_objects"], yicai_cfg.payroll_summary, output_path=final, dry_run=False)
            results = {}
            for vendor, cfg in (("yarun", yarun_cfg), ("yicai", yicai_cfg)):
                records = extracted[vendor].data["record_objects"]
                payroll_records = summary_tool.read_summary(final, records, cfg.payroll_summary)
                results[vendor] = VendorSettlementReconciliationTool().reconcile(records, payroll_records, fields=cfg.reconciliation_fields)
            final_validation = FinalPayrollValidationTool().validate(final, expense_month=context.expense_month)
            if not all(result.success for result in results.values()) or not final_validation.success:
                raise ExcelOperationError("Payroll output did not pass reconciliation/final validation")
            reconciliation_data = {vendor: result.data for vendor, result in results.items()}
            output_cfg = yaml.safe_load((self.project_root / "knowledge" / "field-mappings" / "vendor-payroll-outputs.yaml").read_text(encoding="utf-8"))
            generator = VendorPayrollGenerateTool()
            yicai_output = Path(context.run_directory) / "output" / "vendor_payroll" / f"易才-赛倍明工资（派遣）-{context.expense_month.replace('-', '')}.xlsx"
            yarun_output = Path(context.run_directory) / "output" / "vendor_payroll" / f"亚润-赛倍明薪资表-{context.expense_month.replace('-', '')}.xlsx"
            report(68, "生成易才对接薪资表")
            yicai_generated = generator.generate(final, yicai_output, vendor="yicai", expense_month=context.expense_month, employees=output_cfg["yicai"]["employees"])
            report(84, "生成亚润对接薪资表")
            yarun_generated = generator.generate(final, yarun_output, vendor="yarun", expense_month=context.expense_month, employees=output_cfg["yarun"]["employees"])
            report(94, "保存薪资表并等待第三方回传")
            context.outputs["payroll"] = str(final)
            context.outputs["yicai_payroll"] = str(yicai_output)
            context.outputs["yarun_payroll"] = str(yarun_output)
            context.summaries["reconciliation"] = reconciliation_data
            context.summaries["final_payroll_validation"] = final_validation.data
            context.summaries["vendor_payroll_outputs"] = {"yicai": yicai_generated.data, "yarun": yarun_generated.data}
            context.summaries["required_files"] = [
                {"category": category, "received": len(context.files.get(category, [])) == 1}
                for category in (*self.RETURN_INPUTS, *self.INTERNAL_PAYMENT_INPUTS)
            ]
            context.transition(WorkflowState.WAITING_VENDOR_RETURNS); context.save()
            self._audit(context).log("vendor_payroll_outputs_generated", status="success", details={"yicai": str(yicai_output), "yarun": str(yarun_output), "payroll_sha256": sha256_file(final)})
            return context
        except Exception as error:
            self._fail(context, "payroll_reconciliation_failed", error); raise

    def generate_payment_requests(self, context: AgentContext) -> AgentContext:
        ensure_state(context.current_state, WorkflowState.WAITING_RECONCILIATION_APPROVAL)
        evidence = context.approval_evidence[ApprovalStage.PAYROLL_RECONCILIATION.value]
        if not self._approvals(context).is_approved(ApprovalStage.PAYROLL_RECONCILIATION, evidence_hash=evidence):
            raise ExcelOperationError("Reconciliation approval has not been granted")
        try:
            extracted = self._extract_vendors(context)
            template, template_source = self._payment_request_template(context)
            outputs = {}
            amounts = {}
            today = date.today().isoformat()
            for vendor in ("yarun", "yicai"):
                cfg = load_vendor_config(self.vendor_config_dir / f"{vendor}.yaml", context.expense_month)
                records = extracted[vendor].data["record_objects"]
                amount = sum((record.amount(field) for record in records for field in cfg.reconciliation_fields), Decimal("0.00"))
                output = Path(context.run_directory) / "output" / "payment_requests" / f"{vendor}-payment-{context.expense_month.replace('-', '')}.xlsx"
                result = VendorPaymentRequestTool().generate(template, output, sheet_name=cfg.payment_sheet_name, cell_mapping=cfg.payment_cells, values={"payment_date": today, "vendor_name": cfg.vendor_name, "payment_amount": amount, "purpose": cfg.payment_purpose}, reconciliation_passed=True, dry_run=False)
                outputs[vendor] = result.data["output_path"]; amounts[vendor] = str(amount)
            specifications = [{"path": outputs[vendor], "required_sheets": [load_vendor_config(self.vendor_config_dir / f"{vendor}.yaml", context.expense_month).payment_sheet_name]} for vendor in outputs]
            output_validation = OutputValidationTool().validate(specifications)
            if not output_validation.success:
                raise ExcelOperationError("Generated payment requests failed output validation")
            payment_evidence = evidence_hash({vendor: {"amount": amounts[vendor], "sha256": sha256_file(path)} for vendor, path in outputs.items()})
            context.outputs.update({f"payment_{vendor}": path for vendor, path in outputs.items()})
            context.summaries["payment_amounts"] = amounts
            context.summaries["payment_request_template"] = {
                "path": str(template),
                "source": template_source,
            }
            context.approval_evidence[ApprovalStage.PAYMENT_REQUEST.value] = payment_evidence
            approval = self._approvals(context).create(run_id=context.run_id, expense_month=context.expense_month, stage=ApprovalStage.PAYMENT_REQUEST, summary="确认亚润和易才最终付款单金额", evidence_hash=payment_evidence, details=amounts)
            context.summaries["pending_approval_id"] = approval.id
            context.transition(WorkflowState.WAITING_PAYMENT_APPROVAL); context.save()
            self._audit(context).log(
                "payment_requests_generated",
                status="pending",
                details={
                    "approval_id": approval.id,
                    "amounts": amounts,
                    "template_path": str(template),
                    "template_source": template_source,
                },
            )
            return context
        except Exception as error:
            self._fail(context, "payment_generation_failed", error); raise

    def reconcile_vendor_returns(self, context: AgentContext) -> AgentContext:
        """Compare Yicai (17) and Yarun (18) returned workbooks with the main payroll."""
        ensure_state(context.current_state, WorkflowState.WAITING_VENDOR_RETURNS)
        try:
            payroll_path = context.outputs.get("payroll") or self._one_file(context, "payroll_workbook")
            returned = {
                "yicai": PayrollBillImportTool().extract(self._one_file(context, "payroll_bill"), bill_type="yicai", expense_month=context.expense_month),
                "yarun": PayrollBillImportTool().extract(self._one_file(context, "payroll_export"), bill_type="yarun", expense_month=context.expense_month),
            }
            fields = ["gross_pay", "personal_social_housing", "income_tax", "net_pay"]
            reconciler = EmployeeAmountReconciliationTool()
            results = {}
            for vendor, result in returned.items():
                names = {record.employee_name for record in result.data["record_objects"]}
                main_records = self._read_payroll_amounts(payroll_path, context.expense_month, names)
                results[vendor] = reconciler.reconcile(main_records, result.data["record_objects"], fields=fields)
            reconciliation_data = {vendor: result.data for vendor, result in results.items()}
            recon_evidence = evidence_hash({
                "payroll_sha256": sha256_file(payroll_path),
                "return_files": {category: sha256_file(self._one_file(context, category)) for category in self.RETURN_INPUTS},
                "reconciliation": reconciliation_data,
            })
            context.summaries["vendor_return_reconciliation"] = reconciliation_data
            context.approval_evidence[ApprovalStage.PAYROLL_RECONCILIATION.value] = recon_evidence
            passed = all(result.success for result in results.values())
            approval = self._approvals(context).create(
                run_id=context.run_id, expense_month=context.expense_month,
                stage=ApprovalStage.PAYROLL_RECONCILIATION,
                summary="确认易才和亚润回传工资与主工资表的核对结果",
                evidence_hash=recon_evidence,
                details={vendor: {"passed": result.success, **result.data} for vendor, result in results.items()},
            )
            context.summaries["pending_approval_id"] = approval.id
            context.transition(WorkflowState.WAITING_RECONCILIATION_APPROVAL); context.save()
            self._audit(context).log("vendor_returns_reconciled", status="pending", details={"approval_id": approval.id, "passed": passed})
            return context
        except Exception as error:
            self._fail(context, "vendor_return_reconciliation_failed", error); raise

    def complete(self, context: AgentContext) -> AgentContext:
        ensure_state(context.current_state, WorkflowState.WAITING_PAYMENT_APPROVAL)
        evidence = context.approval_evidence[ApprovalStage.PAYMENT_REQUEST.value]
        if not self._approvals(context).is_approved(ApprovalStage.PAYMENT_REQUEST, evidence_hash=evidence):
            raise ExcelOperationError("Payment approval has not been granted")
        month_sheet = f"{int(context.expense_month.split('-')[1])}月"
        specifications = [{"path": context.outputs["payroll"], "required_sheets": [month_sheet]}]
        specifications.extend([
            {"path": context.outputs["yicai_payroll"], "required_sheets": [f"{int(context.expense_month.split('-')[1])}月派遣"]},
            {"path": context.outputs["yarun_payroll"], "required_sheets": [month_sheet]},
        ])
        for vendor in ("yarun", "yicai"):
            cfg = load_vendor_config(self.vendor_config_dir / f"{vendor}.yaml", context.expense_month)
            specifications.append({"path": context.outputs[f"payment_{vendor}"], "required_sheets": [cfg.payment_sheet_name]})
        for output_key in (
            "payment_internal_social",
            "payment_internal_tax",
            "payment_internal_housing",
        ):
            if output_key not in context.outputs:
                raise ExcelOperationError(f"Required internal payment request is missing: {output_key}")
            specifications.append({"path": context.outputs[output_key], "required_sheets": ["付款单"]})
        validation = OutputValidationTool().validate(specifications)
        if not validation.success:
            self._fail(context, "completion_validation_failed", ExcelOperationError("Final outputs are invalid")); raise ExcelOperationError("Final outputs are invalid")
        report_path = Path(context.run_directory) / "output" / "reports" / "reconciliation-report.json"
        report = ReconciliationReportTool().generate(report_path, run_id=context.run_id, expense_month=context.expense_month, results={"final_outputs": validation, "final_payroll": ToolResult(True, "final_payroll", context.summaries.get("final_payroll_validation", {}))})
        context.outputs["reconciliation_report"] = report.data["output_path"]
        context.summaries.pop("pending_approval_id", None)
        context.transition(WorkflowState.COMPLETED); context.save()
        self._audit(context).log("run_completed", status="success", details={"outputs": context.outputs})
        return context

    def approve(self, context: AgentContext, approval_id: str, *, decided_by: str, comment: str = ""):
        record = self._approvals(context).approve(approval_id, decided_by=decided_by, comment=comment)
        self._audit(context).log("approval_granted", actor=decided_by, status="approved", details={"approval_id": approval_id, "stage": record.stage})
        return record

    def reject(self, context: AgentContext, approval_id: str, *, decided_by: str, comment: str = ""):
        record = self._approvals(context).reject(approval_id, decided_by=decided_by, comment=comment)
        self._audit(context).log("approval_rejected", actor=decided_by, status="rejected", details={"approval_id": approval_id, "stage": record.stage})
        return record

    def _extract_vendors(self, context: AgentContext) -> dict[str, ToolResult[dict]]:
        result = {}
        for vendor, category in (("yarun", "yarun_settlement"), ("yicai", "yicai_dispatch_settlement")):
            cfg = load_vendor_config(self.vendor_config_dir / f"{vendor}.yaml", context.expense_month)
            result[vendor] = VendorCostImportTool().extract(self._one_file(context, category), vendor=cfg.vendor_name, expense_month=context.expense_month, mapping=cfg.settlement)
        return result

    def _extract_vendor_payroll_supporting(self, context: AgentContext) -> dict[str, ToolResult[dict]]:
        """Read only attendance-derived inputs needed before vendor payroll output."""
        attendance = AttendanceImportTool().extract(
            self._one_file(context, "attendance_summary"),
            expense_month=context.expense_month,
        )
        meal_path = (
            Path(context.outputs.get("meal_allowance", ""))
            if context.outputs.get("meal_allowance") else None
        )
        if meal_path is None or not meal_path.is_file():
            generated_path = self.generate_available_meal_allowance(context)
            if generated_path is None:
                raise ExcelOperationError("Attendance is required to generate the meal allowance workbook")
            meal_path = Path(generated_path)
        meal = MealAllowanceImportTool().extract(
            meal_path, expense_month=context.expense_month
        )
        return {"attendance": attendance, "meal": meal}

    def _extract_pre_return_supporting(self, context: AgentContext) -> dict[str, ToolResult[dict]]:
        attendance = AttendanceImportTool().extract(self._one_file(context, "attendance_summary"), expense_month=context.expense_month)
        meal_path = Path(context.outputs.get("meal_allowance", "")) if context.outputs.get("meal_allowance") else None
        if meal_path is None or not meal_path.is_file():
            generated_path = self.generate_available_meal_allowance(context)
            if generated_path is None:
                raise ExcelOperationError("Attendance is required to generate the meal allowance workbook")
            meal_path = Path(generated_path)
        meal = MealAllowanceImportTool().extract(meal_path, expense_month=context.expense_month)
        social = SocialInsuranceImportTool().extract(self._one_file(context, "social_detail"), expense_month=context.expense_month)
        housing = HousingFundImportTool().extract(self._one_file(context, "housing_fund_detail"), expense_month=context.expense_month)
        tax = TaxPaymentImportTool().extract(self._one_file(context, "tax_payment"), expense_month=context.expense_month)
        return {
            "attendance": attendance, "meal": meal, "social": social, "housing": housing, "tax": tax,
        }

    @staticmethod
    def _read_payroll_amounts(payroll_path: str | Path, expense_month: str, employee_names: set[str]) -> list[EmployeeAmountRecord]:
        aliases = {"李伟1": "李伟", "杨悦1": "杨悦", "刘同涛1": "刘同涛", "孙晓霞1": "孙晓霞"}
        records = []
        with ExcelSession(payroll_path, read_only=True) as session:
            sheet = session.sheet(f"{int(expense_month.split('-')[1])}月")
            for row in range(8, 39):
                name = str(sheet.Range(f"C{row}").Text).strip()
                normalized = aliases.get(name, name)
                if normalized not in employee_names:
                    continue
                records.append(EmployeeAmountRecord(normalized, expense_month, {
                    "gross_pay": sheet.Range(f"P{row}").Value2,
                    "personal_social_housing": sheet.Range(f"AE{row}").Value2,
                    "income_tax": sheet.Range(f"AI{row}").Value2,
                    "net_pay": sheet.Range(f"AJ{row}").Value2,
                }))
        return records

    @staticmethod
    def _supporting_summary(supporting: dict[str, ToolResult[dict]]) -> dict:
        summary = {
            "attendance": {"record_count": supporting["attendance"].data["record_count"]},
            "meal": {"record_count": supporting["meal"].data["record_count"], "total": supporting["meal"].data["total"]},
        }
        if "social" in supporting:
            summary["social"] = {
                "record_count": supporting["social"].data["record_count"],
                "total": supporting["social"].data["total"],
            }
        if "housing" in supporting:
            summary["housing"] = {
                "record_count": supporting["housing"].data["record_count"],
                "total": supporting["housing"].data["total"],
            }
        if "tax" in supporting:
            summary["tax"] = supporting["tax"].data
        return summary

    def _vendor_import_evidence(self, context: AgentContext, vendor_summary: dict, supporting_summary: dict) -> str:
        """Bind approval to displayed summaries and immutable input file bytes.

        Derived Excel values are deliberately not recalculated for this check:
        opening a workbook may refresh cached formulas even when its source bytes
        have not changed, which must not produce a false tamper alarm.
        """
        fingerprints = {
            category: [sha256_file(path) for path in sorted(paths)]
            for category, paths in sorted(context.files.items())
            if category in self.VENDOR_PAYROLL_INPUTS
        }
        return evidence_hash({
            "expense_month": context.expense_month,
            "input_files": fingerprints,
            "vendor_summary": vendor_summary,
            "supporting_summary": supporting_summary,
        })

    @staticmethod
    def _read_internal_payroll(
        payroll_path: str | Path,
        expense_month: str,
        employee_names: set[str] | None = None,
    ) -> list[EmployeeAmountRecord]:
        aliases = {"李伟1": "李伟", "杨悦1": "杨悦", "刘同涛1": "刘同涛", "孙晓霞1": "孙晓霞"}
        records = []
        with ExcelSession(payroll_path, read_only=True) as session:
            sheet = session.sheet(f"{int(expense_month.split('-')[1])}月")
            for row in range(8, 39):
                if not isinstance(sheet.Range(f"A{row}").Value2, (int, float)):
                    continue
                name = str(sheet.Range(f"C{row}").Text).strip()
                if not name:
                    continue
                name = aliases.get(name, name)
                values = {
                    "pension_company": sheet.Range(f"R{row}").Value2,
                    "medical_company": sheet.Range(f"S{row}").Value2,
                    "unemployment_company": sheet.Range(f"T{row}").Value2,
                    "injury_company": sheet.Range(f"U{row}").Value2,
                    "supplemental_medical_company": sheet.Range(f"V{row}").Value2,
                    "housing_company": sheet.Range(f"X{row}").Value2,
                    "pension_employee": sheet.Range(f"AA{row}").Value2,
                    "medical_employee": sheet.Range(f"AB{row}").Value2,
                    "unemployment_employee": sheet.Range(f"AC{row}").Value2,
                    "housing_employee": sheet.Range(f"AD{row}").Value2,
                }
                records.append(EmployeeAmountRecord(name, expense_month, values))
        internal_names = {"周丹妮", "李婷婷", "马陆煜", "郑科达", "杨光", "刘钰", "虞铭东", "周琴", "宋付豪", "刘魏", "王艺卢", "吴启红", "王兵强", "徐超导"}
        selected_names = employee_names if employee_names is not None else internal_names
        return [record for record in records if record.employee_name in selected_names]

    @staticmethod
    def _one_file(context: AgentContext, category: str) -> Path:
        paths = context.files.get(category, [])
        if len(paths) != 1:
            raise ExcelOperationError(f"Expected exactly one file for {category}; found {len(paths)}")
        return Path(paths[0])

    def _payment_request_template(self, context: AgentContext) -> tuple[Path, str]:
        """Prefer an explicitly supplied template, otherwise use the bundled default."""
        paths = context.files.get("payment_request_template", [])
        if len(paths) > 1:
            raise ExcelOperationError(
                f"Expected at most one file for payment_request_template; found {len(paths)}"
            )
        if paths:
            return Path(paths[0]), "user_supplied"
        if not self.default_payment_request_template.is_file():
            raise ExcelOperationError(
                f"Default payment request template does not exist: {self.default_payment_request_template}"
            )
        return self.default_payment_request_template, "default"

    @staticmethod
    def _audit(context: AgentContext) -> AuditLogger:
        return AuditLogger(context.run_directory, run_id=context.run_id)

    @staticmethod
    def _approvals(context: AgentContext) -> ApprovalService:
        return ApprovalService(context.run_directory)

    def _fail(self, context: AgentContext, event_type: str, error: Exception) -> None:
        context.last_error = str(error)
        if context.current_state not in {WorkflowState.COMPLETED, WorkflowState.FAILED}:
            context.transition(WorkflowState.FAILED)
        context.save()
        self._audit(context).log(event_type, status="failed", details={"error": str(error)})
