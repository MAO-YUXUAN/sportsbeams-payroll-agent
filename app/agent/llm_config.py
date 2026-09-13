from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml


@dataclass(slots=True)
class LLMConfig:
    provider: str
    base_url: str
    api_key_env: str
    default_model: str
    complex_model: str
    max_requests_per_run: int
    max_input_tokens: int
    max_output_tokens: int
    monthly_budget_cny: Decimal
    pricing: dict[str, dict[str, Decimal]]

    def price(self, model: str, token_type: str) -> Decimal:
        try:
            return self.pricing[model][token_type]
        except KeyError as error:
            raise ValueError(f"No {token_type} price configured for model {model}") from error


def load_llm_config(path: str | Path) -> LLMConfig:
    raw = yaml.safe_load(Path(path).expanduser().resolve().read_text(encoding="utf-8"))
    limits = raw["limits"]
    pricing = {
        model: {kind: Decimal(str(value)) for kind, value in prices.items()}
        for model, prices in raw["pricing_cny_per_million_tokens"].items()
    }
    return LLMConfig(
        provider=str(raw["provider"]),
        base_url=str(raw["base_url"]),
        api_key_env=str(raw["api_key_env"]),
        default_model=str(raw["default_model"]),
        complex_model=str(raw["complex_model"]),
        max_requests_per_run=int(limits["max_requests_per_run"]),
        max_input_tokens=int(limits["max_input_tokens"]),
        max_output_tokens=int(limits["max_output_tokens"]),
        monthly_budget_cny=Decimal(str(limits["monthly_budget_cny"])),
        pricing=pricing,
    )
