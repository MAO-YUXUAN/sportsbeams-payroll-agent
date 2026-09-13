from datetime import datetime
from decimal import Decimal

import pytest

from app.agent import LLMBudgetError, LLMUsageLedger, load_llm_config


def test_load_deepseek_budget_config():
    config = load_llm_config("knowledge/llm.yaml")
    assert config.provider == "deepseek"
    assert config.default_model == "deepseek-v4-flash"
    assert config.monthly_budget_cny == Decimal("20.00")


def test_cost_uses_conservative_configured_price(tmp_path):
    config = load_llm_config("knowledge/llm.yaml")
    ledger = LLMUsageLedger(tmp_path / "usage.jsonl", run_id="run-001")
    cost = ledger.calculate_cost(
        config,
        model="deepseek-v4-flash",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert cost == Decimal("13.200000")


def test_request_limit_blocks_more_calls(tmp_path):
    config = load_llm_config("knowledge/llm.yaml")
    config.max_requests_per_run = 1
    ledger = LLMUsageLedger(tmp_path / "usage.jsonl", run_id="run-001")
    ledger.record(config, model=config.default_model, input_tokens=10, output_tokens=10, success=True)

    with pytest.raises(LLMBudgetError):
        ledger.preflight(config, model=config.default_model, estimated_input_tokens=10)


def test_monthly_budget_blocks_estimated_request(tmp_path):
    config = load_llm_config("knowledge/llm.yaml")
    config.monthly_budget_cny = Decimal("0.000001")
    ledger = LLMUsageLedger(tmp_path / "usage.jsonl", run_id="run-001")

    with pytest.raises(LLMBudgetError):
        ledger.preflight(config, model=config.default_model, estimated_input_tokens=100)
