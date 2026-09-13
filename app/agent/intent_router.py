from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .llm_client import PayrollExplanationClient


READ_ONLY_INTENTS = {"status", "missing_files", "explain", "list_approvals", "list_outputs", "help", "chat"}
MUTATING_INTENTS = {"advance", "approve", "reject", "repair_current_run"}
ALL_INTENTS = READ_ONLY_INTENTS | MUTATING_INTENTS
APPROVAL_ID_PATTERN = re.compile(r"apr_[0-9a-fA-F]+")


@dataclass(slots=True)
class AgentIntent:
    name: str
    parameters: dict[str, Any] = field(default_factory=dict)
    source: str = "local"


class IntentRouter:
    """Route explicit mutations locally; use the LLM only for read-only ambiguity."""

    def __init__(self, llm: PayrollExplanationClient | None = None):
        self.llm = llm

    def route(self, message: str) -> AgentIntent:
        text = message.strip()
        lowered = text.casefold()
        approval_id = self._approval_id(text)

        explicit_approve = bool(re.match(r"^(?:请)?(?:批准|同意审批|审批通过)\s+apr_[0-9a-fA-F]+", text)) or bool(re.match(r"^approve\s+apr_[0-9a-fA-F]+", lowered))
        explicit_reject = bool(re.match(r"^(?:请)?(?:拒绝|驳回审批|审批不通过)\s+apr_[0-9a-fA-F]+", text)) or bool(re.match(r"^reject\s+apr_[0-9a-fA-F]+", lowered))
        if explicit_approve:
            return AgentIntent("approve", {"approval_id": approval_id, "comment": text})
        if explicit_reject:
            return AgentIntent("reject", {"approval_id": approval_id, "comment": text})
        if text in {
            "修复当前任务",
            "修复当前工资表",
            "重新检查并修复工资表",
            "恢复缺失的工作表",
            "恢复缺失Sheet",
            "恢复缺失 Sheet",
        }:
            return AgentIntent("repair_current_run")
        if text in {"继续", "推进", "执行下一步", "继续执行", "下一步"} or lowered in {"advance", "continue", "next"}:
            return AgentIntent("advance")
        if any(word in text for word in ("待审批", "审批列表", "哪些审批")):
            return AgentIntent("list_approvals")
        if any(word in text for word in ("输出文件", "结果文件", "生成了什么")):
            return AgentIntent("list_outputs")
        if any(word in text for word in ("缺哪些文件", "缺少哪些文件", "还缺什么文件", "需要哪些文件", "未收到文件")):
            return AgentIntent("missing_files")
        if any(word in text for word in ("状态", "进行到", "当前步骤")):
            return AgentIntent("status")
        if any(word in text for word in ("为什么", "解释", "差异", "异常", "失败原因")):
            return AgentIntent("explain", {"question": text})
        if lowered in {"help", "?", "帮助"}:
            return AgentIntent("help")

        # Anything that is not an explicit deterministic command is ordinary
        # conversation. The LLM may answer it, but may never turn it into a
        # mutating action.
        return AgentIntent("chat", {"question": text})

    def _route_read_only_with_llm(self, message: str) -> AgentIntent:
        response = self.llm.explain(
            {"allowed_intents": sorted(READ_ONLY_INTENTS), "user_message": message},
            question=(
                "只做只读意图分类。返回一个JSON对象，格式为"
                '{"name":"status|explain|list_approvals|list_outputs|help"}。'
                "不得返回执行、批准、拒绝或其他动作。"
            ),
        )
        try:
            candidate = response[response.index("{") : response.rindex("}") + 1]
            name = str(json.loads(candidate).get("name", "help"))
        except (ValueError, json.JSONDecodeError, AttributeError):
            name = "help"
        if name not in READ_ONLY_INTENTS:
            name = "help"
        return AgentIntent(name, {"question": message} if name == "explain" else {}, source="llm")

    @staticmethod
    def _approval_id(message: str) -> str | None:
        match = APPROVAL_ID_PATTERN.search(message)
        return match.group(0) if match else None
