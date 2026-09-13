from __future__ import annotations

import hashlib
import json
from typing import Any


def evidence_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def public_tool_data(data: dict) -> dict:
    return {key: value for key, value in data.items() if key != "record_objects"}
