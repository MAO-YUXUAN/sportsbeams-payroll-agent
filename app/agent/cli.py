from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .orchestrator import PayrollAgent
from .conversation import PayrollConversation
from .llm_client import PayrollExplanationClient
from .llm_config import load_llm_config
from .llm_usage import LLMUsageLedger
from app.security.setup import prompt_for_deepseek_api_key


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sportsbeams-payroll")
    parser.add_argument("--project-root", default=str(Path(__file__).parents[2]))
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--month", required=True)
    create.add_argument("--source", required=True)
    create.add_argument("--run-id")
    advance = commands.add_parser("advance")
    advance.add_argument("--run", required=True)
    status = commands.add_parser("status")
    status.add_argument("--run", required=True)
    approve = commands.add_parser("approve")
    approve.add_argument("--run", required=True)
    approve.add_argument("--approval-id", required=True)
    approve.add_argument("--by", required=True)
    approve.add_argument("--comment", default="")
    reject = commands.add_parser("reject")
    reject.add_argument("--run", required=True)
    reject.add_argument("--approval-id", required=True)
    reject.add_argument("--by", required=True)
    reject.add_argument("--comment", default="")
    chat = commands.add_parser("chat")
    chat.add_argument("--run", required=True)
    chat.add_argument("--message")
    chat.add_argument("--actor", default="user")
    chat.add_argument("--model")
    commands.add_parser("setup-llm")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "setup-llm":
        return 0 if prompt_for_deepseek_api_key(force=True) else 1
    agent = PayrollAgent(args.project_root)
    if args.command == "create":
        context = agent.create_run(expense_month=args.month, source_directory=args.source, run_id=args.run_id)
    else:
        context = agent.load(args.run)
        if args.command == "advance":
            context = agent.advance(context)
        elif args.command == "approve":
            agent.approve(context, args.approval_id, decided_by=args.by, comment=args.comment)
            context = agent.load(args.run)
        elif args.command == "reject":
            agent.reject(context, args.approval_id, decided_by=args.by, comment=args.comment)
            context = agent.load(args.run)
        elif args.command == "chat":
            llm_config = load_llm_config(Path(args.project_root) / "knowledge" / "llm.yaml")
            if llm_config.provider.casefold() == "deepseek":
                prompt_for_deepseek_api_key()
            ledger = LLMUsageLedger(Path(args.project_root) / "data" / "llm-usage.jsonl", run_id=context.run_id)
            conversation = PayrollConversation(
                agent,
                llm=PayrollExplanationClient(model=args.model, config=llm_config, usage_ledger=ledger),
            )
            if args.message:
                print(json.dumps(conversation.handle(context, args.message, actor=args.actor), ensure_ascii=False, indent=2, default=str))
                return 0
            while True:
                message = input("payroll> ").strip()
                if message.casefold() in {"exit", "quit", "退出"}:
                    return 0
                result = conversation.handle(context, message, actor=args.actor)
                print(result["message"])
                context = agent.load(args.run)
    print(json.dumps(asdict(context), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
