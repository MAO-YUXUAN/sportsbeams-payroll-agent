from __future__ import annotations

import json
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .llm_config import LLMConfig


class LLMBudgetError(RuntimeError):
    pass


class LLMUsageLedger:
    def __init__(self, path: str | Path, *, run_id: str):
        self.path = Path(path).expanduser().resolve()
        self.run_id = run_id

    def preflight(self, config: LLMConfig, *, model: str, estimated_input_tokens: int) -> None:
        records = self.read()
        run_requests = sum(record.get("run_id") == self.run_id for record in records)
        if run_requests >= config.max_requests_per_run:
            raise LLMBudgetError(f"LLM request limit reached for run {self.run_id}")
        if estimated_input_tokens > config.max_input_tokens:
            raise LLMBudgetError(
                f"Estimated input tokens {estimated_input_tokens} exceed limit {config.max_input_tokens}"
            )
        month = datetime.now().astimezone().strftime("%Y-%m")
        spent = sum(
            (Decimal(str(record.get("cost_cny", "0"))) for record in records if record.get("month") == month),
            Decimal("0"),
        )
        estimated = self.calculate_cost(
            config,
            model=model,
            input_tokens=estimated_input_tokens,
            output_tokens=config.max_output_tokens,
        )
        if spent + estimated > config.monthly_budget_cny:
            raise LLMBudgetError(
                f"Monthly LLM budget would be exceeded: spent {spent}, estimated request {estimated}, "
                f"budget {config.monthly_budget_cny} CNY"
            )

    def record(self, config: LLMConfig, *, model: str, input_tokens: int, output_tokens: int, success: bool, error_type: str | None = None) -> dict[str, Any]:
        now = datetime.now().astimezone()
        record = {
            "timestamp": now.isoformat(timespec="seconds"),
            "month": now.strftime("%Y-%m"),
            "run_id": self.run_id,
            "provider": config.provider,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_cny": str(self.calculate_cost(config, model=model, input_tokens=input_tokens, output_tokens=output_tokens) if success else Decimal("0")),
            "success": success,
            "error_type": error_type,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush(); os.fsync(stream.fileno())
        return record

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    @staticmethod
    def calculate_cost(config: LLMConfig, *, model: str, input_tokens: int, output_tokens: int) -> Decimal:
        million = Decimal("1000000")
        cost = Decimal(input_tokens) / million * config.price(model, "input")
        cost += Decimal(output_tokens) / million * config.price(model, "output")
        return cost.quantize(Decimal("0.000001"))
