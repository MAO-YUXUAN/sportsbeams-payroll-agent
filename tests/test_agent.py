from pathlib import Path

import pytest

from app.agent import AgentContext, PayrollAgent, WorkflowState, WorkflowStateError
from app.agent.workflow import evidence_hash


def test_context_persists_and_loads(tmp_path):
    context = AgentContext("run-001", "2026-07", str(tmp_path), str(tmp_path / "run-001"))
    context.summaries["count"] = 4
    context.save()

    loaded = AgentContext.load(context.run_directory)

    assert loaded.run_id == "run-001"
    assert loaded.summaries["count"] == 4


def test_state_machine_rejects_skipping_approval_stage(tmp_path):
    context = AgentContext("run-001", "2026-07", str(tmp_path), str(tmp_path / "run-001"))
    with pytest.raises(WorkflowStateError):
        context.transition(WorkflowState.WAITING_RECONCILIATION_APPROVAL)


def test_evidence_hash_is_order_stable():
    assert evidence_hash({"b": 2, "a": 1}) == evidence_hash({"a": 1, "b": 2})


def test_runtime_inputs_exclude_generated_files(tmp_path):
    agent = PayrollAgent(tmp_path)
    assert "meal_allowance" not in agent.RUNTIME_INPUTS
    assert "yicai_payroll" not in agent.RUNTIME_INPUTS
    assert "yarun_payroll" not in agent.RUNTIME_INPUTS
    assert "payment_request_template" not in agent.RUNTIME_INPUTS
    assert agent.RETURN_INPUTS == ("payroll_bill", "payroll_export")
    assert "social_detail" in agent.RUNTIME_INPUTS
    assert agent.VENDOR_PAYROLL_INPUTS == (
        "yarun_settlement", "yicai_dispatch_settlement", "attendance_summary",
    )
    assert agent.INTERNAL_PAYMENT_INPUTS == (
        "social_detail", "tax_payment", "housing_fund_detail",
    )


def test_internal_payment_files_do_not_block_vendor_payroll_stage(tmp_path):
    agent = PayrollAgent(tmp_path)
    context = AgentContext("run-001", "2026-07", str(tmp_path), str(tmp_path / "run-001"))
    context.files = {
        "yarun_settlement": ["yarun.xlsx"],
        "yicai_dispatch_settlement": ["yicai.xlsx"],
        "attendance_summary": ["attendance.xlsx"],
    }

    assert agent.missing_runtime_inputs(context) == []


def test_internal_payment_files_are_required_after_vendor_payroll_generation(tmp_path):
    agent = PayrollAgent(tmp_path)
    context = AgentContext(
        "run-001", "2026-07", str(tmp_path), str(tmp_path / "run-001"),
        state=WorkflowState.WAITING_VENDOR_RETURNS.value,
    )
    context.files = {
        "payroll_bill": ["yicai-return.xlsx"],
        "payroll_export": ["yarun-return.xlsx"],
    }

    assert agent.missing_return_inputs(context) == [
        "social_detail", "tax_payment", "housing_fund_detail",
    ]
    assert [item["category"] for item in agent.input_requirements(context)] == [
        "payroll_bill", "payroll_export",
        "social_detail", "tax_payment", "housing_fund_detail",
    ]


def test_payment_request_uses_bundled_default_unless_user_supplies_override(tmp_path):
    project = tmp_path / "project"
    default_template = project / "templates" / "payment" / "default-payment-request.xlsx"
    default_template.parent.mkdir(parents=True)
    default_template.write_bytes(b"default")
    agent = PayrollAgent(project)
    context = AgentContext("run-001", "2026-07", str(tmp_path), str(tmp_path / "run-001"))

    selected, source = agent._payment_request_template(context)
    assert selected == default_template
    assert source == "default"

    override = tmp_path / "custom-payment-request.xlsx"
    override.write_bytes(b"custom")
    context.files["payment_request_template"] = [str(override)]
    selected, source = agent._payment_request_template(context)
    assert selected == override
    assert source == "user_supplied"


def test_vendor_inputs_are_independent_categories(tmp_path):
    agent = PayrollAgent(tmp_path)
    assert agent.VENDOR_INPUTS == {
        "yarun": "yarun_settlement",
        "yicai": "yicai_dispatch_settlement",
    }


def test_vendor_return_wait_is_between_import_and_reconciliation(tmp_path):
    context = AgentContext("run-001", "2026-07", str(tmp_path), str(tmp_path / "run-001"), state=WorkflowState.WAITING_IMPORT_APPROVAL.value)
    context.transition(WorkflowState.WAITING_VENDOR_RETURNS)
    context.transition(WorkflowState.WAITING_RECONCILIATION_APPROVAL)
    assert context.current_state == WorkflowState.WAITING_RECONCILIATION_APPROVAL


def test_waiting_additional_inputs_is_a_valid_transition(tmp_path):
    context = AgentContext("run-001", "2026-07", str(tmp_path), str(tmp_path / "run-001"), state=WorkflowState.INPUTS_PREPARED.value)
    context.transition(WorkflowState.WAITING_ADDITIONAL_INPUTS)
    assert context.current_state == WorkflowState.WAITING_ADDITIONAL_INPUTS


def test_agent_create_run_writes_state_and_audit(tmp_path):
    project = tmp_path / "project"
    source = tmp_path / "source"
    source.mkdir(); project.mkdir()
    agent = PayrollAgent(project)

    context = agent.create_run(expense_month="2026-07", source_directory=source, run_id="run-test")

    assert context.current_state == WorkflowState.CREATED
    assert Path(context.run_directory, "state.json").is_file()
    assert Path(context.run_directory, "audit", "events.jsonl").is_file()


def test_vendor_import_evidence_is_stable_and_changes_with_input_bytes(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"version-one")
    agent = PayrollAgent(project)
    context = AgentContext("run-001", "2026-07", str(tmp_path), str(tmp_path / "run-001"))
    context.files = {"yarun_settlement": [str(source)]}
    vendors = {"yarun": {"record_count": 1}}
    supporting = {"meal": {"total": "100.00"}}
    first = agent._vendor_import_evidence(context, vendors, supporting)
    assert first == agent._vendor_import_evidence(context, vendors, supporting)
    source.write_bytes(b"version-two")
    assert first != agent._vendor_import_evidence(context, vendors, supporting)
