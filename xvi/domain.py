"""Dependency-light, explicit data semantics."""
from __future__ import annotations

import ast
import json
import math
import re
from datetime import datetime, timezone
from typing import Any

# Ordered presets for Auto and reusable market-wide caches.
LEVELS = {"raw": 0, "30s": 30, "1m": 60, "2m": 120, "5m": 300,
          "10m": 600, "15m": 900, "30m": 1800, "1h": 3600,
          "4h": 14400, "1d": 86400}
MAX_CUSTOM_MINUTES = 1440


def normalize_level(value: str, *, allow_auto: bool = False) -> str:
    """Validate granularity before SQL/path use; canonicalize equivalent presets.

    Custom intervals are whole minutes (1..1440), aligned to Unix epoch UTC.
    Manual intervals never fall back silently to Auto or another resolution.
    """
    if value == "auto" and allow_auto:
        return value
    if isinstance(value, str) and value in LEVELS:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,3}m", value):
        minutes = int(value[:-1])
        if minutes <= MAX_CUSTOM_MINUTES:
            seconds = minutes * 60
            return next((name for name, size in LEVELS.items() if size == seconds), value)
    raise ValueError("Unknown resolution. Choose Auto, raw, 30s, a preset, or 1–1440 whole minutes (e.g. 2m or 7m).")


def level_seconds(value: str) -> int:
    value = normalize_level(value)
    return LEVELS[value] if value in LEVELS else int(value[:-1]) * 60

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


def parse_wallets(value: str | tuple[str, ...] | list[str] | None = None) -> tuple[str, ...]:
    """Exact EVM addresses, case-insensitive. Multiple addresses form an OR filter."""
    if value is None:
        return ()
    if isinstance(value, str):
        if len(value) > 8192:
            raise ValueError("Wallet filter is too long (maximum 8,192 characters).")
        parts = re.split(r"[\s,;]+", value.strip()) if value.strip() else []
    else:
        parts = list(value)
    result = set()
    for part in parts:
        if not isinstance(part, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", part, re.IGNORECASE):
            raise ValueError("Use full wallet addresses: 0x followed by 40 hexadecimal characters.")
        result.add(part.lower())
    if len(result) > 50:
        raise ValueError("Filter at most 50 distinct wallet addresses at a time.")
    return tuple(sorted(result))


def wallet_predicate(wallets: tuple[str, ...], role: str = "either") -> tuple[str, list[str]]:
    """One shared, parameter-bound predicate for series, tape, and exports.

    Never join one row per address: a fill matching both sides still counts once.
    Role describes the recorded fill side, not a human identity or a position.
    """
    wallets = parse_wallets(wallets)
    if role not in {"either", "maker", "taker"}:
        raise ValueError("Wallet role must be either, maker, or taker.")
    if not wallets:
        return "TRUE", []
    placeholders = ",".join("?" for _ in wallets)
    if role == "either":
        return f"(lower(maker) IN ({placeholders}) OR lower(taker) IN ({placeholders}))", list(wallets) * 2
    # Column names come only from the fixed allowlist above, not from user SQL.
    return f"lower({role}) IN ({placeholders})", list(wallets)
