from app.agent import AgentContext, IntentRouter, PayrollConversation


def test_router_requires_explicit_mutation_words():
    router = IntentRouter()
    assert router.route("继续").name == "advance"
    assert router.route("现在是什么状态").name == "status"


def test_router_extracts_approval_id_only_for_explicit_approval():
    router = IntentRouter()
    intent = router.route("批准 apr_abcdef，金额确认无误")
    assert intent.name == "approve"
    assert intent.parameters["approval_id"] == "apr_abcdef"


def test_unknown_message_routes_to_safe_chat():
    assert IntentRouter().route("随便聊聊").name == "chat"


def test_repair_requires_an_explicit_command():
    router = IntentRouter()
    assert router.route("修复当前任务").name == "repair_current_run"
    assert router.route("这个任务需要修复吗？").name != "repair_current_run"


def test_missing_files_is_a_deterministic_command():
    assert IntentRouter().route("目前还缺哪些文件").name == "missing_files"


def test_missing_files_uses_current_workflow_stage(tmp_path):
    context = AgentContext("run-001", "2026-07", str(tmp_path), str(tmp_path / "run-001"), state="waiting_additional_inputs")
    agent = type("Agent", (), {"input_requirements": lambda self, ctx: [
        {"category": "attendance_summary", "received": False},
        {"category": "social_detail", "received": True},
    ]})()
    result = PayrollConversation(agent=agent).handle(context, "还缺哪些文件")
    assert result["intent"]["name"] == "missing_files"
    assert "考勤月度汇总" in result["message"]
    assert "社保明细" not in result["message"]


def test_discussing_approval_does_not_execute_it():
    assert IntentRouter().route("这个审批可以批准吗？apr_abcdef").name != "approve"


def test_conversation_returns_deterministic_status(tmp_path):
    context = AgentContext("run-001", "2026-07", str(tmp_path), str(tmp_path / "run-001"))
    conversation = PayrollConversation(agent=object())

    result = conversation.handle(context, "现在是什么状态")

    assert result["intent"]["name"] == "status"
    assert result["data"]["state"] == "created"


def test_conversation_executes_controlled_current_run_repair(tmp_path):
    context = AgentContext("run-001", "2026-07", str(tmp_path), str(tmp_path / "run-001"))

    class RepairAgent:
        def repair_current_run(self, received_context):
            assert received_context is context
            return {"status": "repaired", "restored_sheets": ["各部门统计表"]}

    result = PayrollConversation(agent=RepairAgent()).handle(context, "修复当前任务")

    assert result["intent"]["name"] == "repair_current_run"
    assert result["data"]["executed"] is True
    assert "各部门统计表" in result["message"]


def test_chat_without_available_llm_returns_clear_message(tmp_path):
    context = AgentContext("run-001", "2026-07", str(tmp_path), str(tmp_path / "run-001"))
    result = PayrollConversation(agent=object()).handle(context, "你好")
    assert result["intent"]["name"] == "chat"
    assert "DeepSeek当前不可用" in result["message"]


class FakeLLM:
    api_key = "configured"

    def explain(self, structured_result, *, question):
        return f"自然回答：{question}"


def test_chat_uses_llm_for_non_command_message(tmp_path):
    context = AgentContext("run-001", "2026-07", str(tmp_path), str(tmp_path / "run-001"))
    result = PayrollConversation(agent=object(), llm=FakeLLM()).handle(context, "你是什么模型")
    assert result["message"] == "自然回答：你是什么模型"


def test_conversation_collects_new_employee_information_without_llm(tmp_path):
    context = AgentContext("run-001", "2026-07", str(tmp_path), str(tmp_path / "run-001"))
    context.summaries["new_employee_requests"] = [{
        "id": "new_employee_2026-07_刘同涛",
        "employee_name": "刘同涛",
        "status": "awaiting_information",
        "answers": {},
        "required_fields": [
            {"key": "department", "label": "所属部门"},
            {"key": "employment_entity", "label": "用工/结算主体"},
            {"key": "hire_date", "label": "入职日期"},
            {"key": "base_salary", "label": "月基础工资"},
            {"key": "payroll_method", "label": "首月计薪方式"},
        ],
    }]

    result = PayrollConversation(agent=object()).handle(
        context, "刘同涛，销售部，亚润，2026-07-01，基础工资8000，按整月计薪"
    )

    request = result["data"]["request"]
    assert result["intent"]["name"] == "provide_new_employee_information"
    assert request["status"] == "ready_for_preview"
    assert request["answers"]["department"] == "销售部"
    assert request["answers"]["employment_entity"] == "亚润"
    assert request["answers"]["hire_date"] == "2026-07-01"
    assert request["answers"]["base_salary"] == "8000.00"
    assert request["answers"]["payroll_method"] == "full_month"


def test_conversation_requires_explicit_confirmation_to_add_new_employee(tmp_path):
    context = AgentContext("run-001", "2026-07", str(tmp_path), str(tmp_path / "run-001"))

    class AddAgent:
        def confirm_new_employee(self, received_context, employee_name, *, actor):
            assert received_context is context
            assert employee_name == "刘同涛"
            assert actor == "舒凯彪"
            return {"status": "completed", "row": 25}

    result = PayrollConversation(agent=AddAgent()).handle(
        context, "确认新增刘同涛", actor="舒凯彪"
    )

    assert result["intent"]["name"] == "confirm_new_employee"
    assert result["data"]["executed"] is True
    assert "第 25 行" in result["message"]


def test_conversation_requires_explicit_confirmation_to_apply_adjustments(tmp_path):
    context = AgentContext("run-001", "2026-07", str(tmp_path), str(tmp_path / "run-001"))

    class AdjustmentAgent:
        def apply_attendance_adjustments(self, received_context, *, actor):
            assert received_context is context
            assert actor == "舒凯彪"
            return {
                "record_count": 25,
                "total_bonus": "5000.00",
                "total_absence_deduction": "367.82",
            }

    result = PayrollConversation(agent=AdjustmentAgent()).handle(
        context, "确认写入补扣款", actor="舒凯彪"
    )

    assert result["intent"]["name"] == "apply_attendance_adjustments"
    assert result["data"]["executed"] is True
    assert "共 25 人" in result["message"]
