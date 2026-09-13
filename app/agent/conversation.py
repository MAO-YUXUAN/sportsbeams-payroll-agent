from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from app.approvals import ApprovalService, ApprovalStatus

from .context import AgentContext
from .intent_router import AgentIntent, IntentRouter
from .llm_client import PayrollExplanationClient
from .orchestrator import PayrollAgent


class PayrollConversation:
    def __init__(self, agent: PayrollAgent, *, llm: PayrollExplanationClient | None = None):
        self.agent = agent
        self.llm = llm
        self.router = IntentRouter(llm)

    def handle(self, context: AgentContext, message: str, *, actor: str = "user") -> dict[str, Any]:
        if message.strip() in {"确认写入补扣款", "确认补扣款", "确认写入考勤补扣款"}:
            result = self.agent.apply_attendance_adjustments(context, actor=actor)
            intent = AgentIntent("apply_attendance_adjustments")
            return self._response(
                intent,
                f"补扣款已写入工资表，共 {result.get('record_count')} 人；"
                f"奖金/补贴合计 {result.get('total_bonus')} 元，"
                f"缺勤扣款合计 {result.get('total_absence_deduction')} 元。原文件已自动备份。",
                {"executed": True, "result": result},
            )
        confirm_new_employee = re.fullmatch(r"(?:请)?确认新增\s*([^，,。\s]+)", message.strip())
        if confirm_new_employee:
            employee_name = confirm_new_employee.group(1)
            result = self.agent.confirm_new_employee(context, employee_name, actor=actor)
            intent = AgentIntent("confirm_new_employee", {"employee_name": employee_name})
            return self._response(
                intent,
                f"已将 {employee_name} 正式新增到本月工资表第 {result.get('row')} 行，"
                "并完成公式重算与小计校验。原文件已自动备份。",
                {"executed": True, "result": result},
            )
        onboarding = self._capture_new_employee_information(context, message)
        if onboarding is not None:
            return onboarding
        intent = self.router.route(message)
        if intent.name == "status":
            return self._response(intent, f"当前状态：{context.state}", {"state": context.state, "pending_approval_id": context.summaries.get("pending_approval_id"), "last_error": context.last_error})
        if intent.name == "list_approvals":
            records = ApprovalService(context.run_directory).list(status=ApprovalStatus.PENDING)
            return self._response(intent, f"当前有 {len(records)} 个待审批事项。", {"approvals": [record.to_dict() for record in records]})
        if intent.name == "list_outputs":
            return self._response(intent, f"当前有 {len(context.outputs)} 个输出文件。", {"outputs": context.outputs})
        if intent.name == "missing_files":
            labels = {
                "yarun_settlement": "亚润结算表", "yicai_dispatch_settlement": "易才派遣社保账单",
                "payment_request_template": "社保公积金付款单模板", "attendance_summary": "考勤月度汇总",
                "social_detail": "社保明细", "tax_payment": "个税付款资料", "housing_fund_detail": "公积金明细",
                "payroll_bill": "易才回传工资账单", "payroll_export": "亚润回传工资表",
            }
            requirements = self.agent.input_requirements(context)
            missing = [labels.get(item["category"], item["category"]) for item in requirements if not item["received"]]
            if missing:
                message_text = "当前阶段还缺少：\n" + "\n".join(f"- {name}" for name in missing)
            else:
                message_text = "当前阶段所需文件已经全部收到，可以继续处理。"
            return self._response(intent, message_text, {"missing_files": missing, "requirements": requirements})
        if intent.name == "advance":
            updated = self.agent.advance(context)
            return self._response(intent, f"流程已推进到：{updated.state}", {"state": updated.state, "pending_approval_id": updated.summaries.get("pending_approval_id")})
        if intent.name == "repair_current_run":
            result = self.agent.repair_current_run(context)
            restored = result.get("restored_sheets", [])
            if restored:
                message_text = "当前任务修复完成，已恢复工作表：" + "、".join(restored)
            elif result.get("status") == "already_valid":
                message_text = "检查完成，当前工资表结构完整，不需要修复。"
            else:
                message_text = "当前任务修复完成，工作表顺序已校正。"
            return self._response(intent, message_text, {"executed": True, "repair": result})
        if intent.name in {"approve", "reject"}:
            approval_id = intent.parameters.get("approval_id")
            if not approval_id:
                return self._response(intent, "请在消息中提供完整审批编号，例如 apr_xxx。", {"executed": False})
            if intent.name == "approve":
                record = self.agent.approve(context, approval_id, decided_by=actor, comment=intent.parameters.get("comment", ""))
                message_text = "审批已批准。"
            else:
                record = self.agent.reject(context, approval_id, decided_by=actor, comment=intent.parameters.get("comment", ""))
                message_text = "审批已拒绝。"
            return self._response(intent, message_text, {"executed": True, "approval": record.to_dict()})
        if intent.name in {"explain", "chat"}:
            structured = {"state": context.state, "summaries": context.summaries, "outputs": context.outputs, "last_error": context.last_error}
            if self.llm and self.llm.api_key:
                try:
                    explanation = self.llm.explain(structured, question=intent.parameters.get("question", message))
                except Exception:
                    explanation = self._llm_unavailable_message(context, include_local=intent.name == "explain")
            else:
                explanation = self._llm_unavailable_message(context, include_local=intent.name == "explain")
            return self._response(intent, explanation, structured)
        return self._response(intent, "可用指令：查看状态、查看待审批、查看输出文件、解释异常、继续、修复当前任务、批准 apr_xxx、拒绝 apr_xxx。", {"allowed_actions": ["status", "list_approvals", "list_outputs", "explain", "advance", "repair_current_run", "approve", "reject"]})

    def _capture_new_employee_information(self, context: AgentContext, message: str) -> dict[str, Any] | None:
        requests = context.summaries.get("new_employee_requests", [])
        pending = [item for item in requests if isinstance(item, dict) and item.get("status") == "awaiting_information"]
        if not pending:
            return None
        request = next((item for item in pending if str(item.get("employee_name")) in message), None)
        if request is None and len(pending) == 1 and any(
            token in message for token in ("基础工资", "月薪", "入职", "计薪", "亚润", "易才", "公司")
        ):
            request = pending[0]
        if request is None:
            return None

        answers = dict(request.get("answers", {}))
        name = str(request["employee_name"])
        department = re.search(r"(?:所属)?部门[：:\s]*([^，,；;\n]+)", message)
        if department:
            answers["department"] = department.group(1).strip()
        else:
            for candidate in ("销售部", "销售行政部", "工程技术部", "市场部", "总办"):
                if candidate in message:
                    answers["department"] = candidate
                    break
        entity = re.search(r"(?:用工|结算)?主体[：:\s]*(公司|亚润|易才)", message)
        if entity:
            answers["employment_entity"] = entity.group(1)
        else:
            for candidate in ("亚润", "易才", "公司"):
                if candidate in message:
                    answers["employment_entity"] = candidate
                    break
        hire_date = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", message)
        if hire_date:
            try:
                answers["hire_date"] = datetime(
                    int(hire_date.group(1)), int(hire_date.group(2)), int(hire_date.group(3))
                ).date().isoformat()
            except ValueError:
                pass
        salary = re.search(r"(?:基础工资|月薪|月基础工资)[：:\s]*(?:人民币|￥|¥)?\s*([\d,]+(?:\.\d{1,2})?)", message)
        if salary:
            try:
                value = Decimal(salary.group(1).replace(",", ""))
                if value > 0:
                    answers["base_salary"] = str(value.quantize(Decimal("0.01")))
            except InvalidOperation:
                pass
        if "整月" in message:
            answers["payroll_method"] = "full_month"
        elif any(token in message for token in ("入职日折算", "按入职日", "折算")):
            answers["payroll_method"] = "prorated_from_hire_date"

        request["answers"] = answers
        for notification in context.summaries.get("agent_notifications", []):
            if isinstance(notification, dict) and notification.get("employee_name") == name:
                notification["status"] = "acknowledged"
        missing = [field["label"] for field in request.get("required_fields", []) if field["key"] not in answers]
        if missing:
            reply = f"已记录 {name} 的部分资料。还需要：" + "、".join(missing) + "。"
        else:
            request["status"] = "ready_for_preview"
            reply = (
                f"{name} 的资料已收齐：{answers['department']}、{answers['employment_entity']}、"
                f"入职 {answers['hire_date']}、月基础工资 {answers['base_salary']} 元、"
                f"{'整月计薪' if answers['payroll_method'] == 'full_month' else '按入职日折算'}。"
                "新增预览已生成。确认无误请回复：确认新增" + name + "。"
            )
        context.summaries["new_employee_requests"] = requests
        context.save()
        intent = AgentIntent("provide_new_employee_information", {"employee_name": name})
        return self._response(intent, reply, {"request": request, "missing_fields": missing})

    @staticmethod
    def _response(intent: AgentIntent, message: str, data: dict[str, Any]) -> dict[str, Any]:
        return {"intent": asdict(intent), "message": message, "data": data}

    @staticmethod
    def _local_explanation(context: AgentContext) -> str:
        if context.last_error:
            return f"流程状态为 {context.state}。最近错误：{context.last_error}"
        pending = context.summaries.get("pending_approval_id")
        if pending:
            return f"流程状态为 {context.state}，正在等待审批 {pending}。"
        return f"流程状态为 {context.state}，当前没有记录到阻塞错误。"

    @classmethod
    def _llm_unavailable_message(cls, context: AgentContext, *, include_local: bool) -> str:
        prefix = f"{cls._local_explanation(context)}\n\n" if include_local else ""
        return prefix + "DeepSeek当前不可用，可能是账户余额不足、网络异常或API配置问题。工资核算和固定命令仍可正常使用。"
