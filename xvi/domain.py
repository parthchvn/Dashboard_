"""Dependency-light, explicit data semantics."""
from __future__ import annotations

import ast
import json
import math
import re
from datetime import datetime, timezone
from typing import Any

LEVELS = {"raw": 0, "30s": 30, "1m": 60, "5m": 300, "1h": 3600, "1d": 86400}
SCHEMA_VERSION = 1
EXCHANGES = (
    "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
    "0xc5d563a36ae78145c45a50134d48a1215220f80a",
)


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", str(value)):
        raise ValueError("IDs must contain 1–128 letters, numbers, underscores or hyphens.")
    return str(value)


def normalize(price: float, token: str, direction: str) -> tuple[float, str]:
    """Normalize ORIGINAL token prices exactly once; leave cash unchanged."""
    if not math.isfinite(price) or not 0 <= price <= 1:
        raise ValueError("Price must be finite and between zero and one.")
    if token not in {"token1", "token2"} or direction not in {"BUY", "SELL"}:
        raise ValueError("Original token side and taker direction are required.")
    if token == "token1":
        return price, direction
    return 1 - price, "SELL" if direction == "BUY" else "BUY"


def epoch(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            return int(value)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return int((dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).timestamp())
    except (ValueError, TypeError, OverflowError):
        return None


def metadata_outcome(closed: Any, prices: Any, answer1: str, answer2: str) -> str | None:
    """Conservative metadata indication, NOT proof of on-chain settlement."""
    if closed not in (True, 1, "1", "true", "True"):
        return None
    try:
        if isinstance(prices, str):
            if len(prices) > 256:
                return None
            try:
                prices = json.loads(prices)
            except json.JSONDecodeError:
                prices = ast.literal_eval(prices)  # The card also uses ['1', '0'].
        if not isinstance(prices, (list, tuple)) or len(prices) != 2:
            return None
        p = [float(x) for x in prices]
        return answer1 if p == [1.0, 0.0] else answer2 if p == [0.0, 1.0] else None
    except (ValueError, TypeError, SyntaxError):
        return None


def choose_level(count: int, span: int, target: int) -> str:
    if count <= target:
        return "raw"
    for name, seconds in list(LEVELS.items())[1:]:
        if math.ceil(span / seconds) <= target:
            return name
    return "1d"


def clean_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: clean_json(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [clean_json(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return value
