"""Phase 2 signal contracts and state-machine invariants."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import uuid
from typing import Any


SIGNAL_STATUSES = (
    "WATCH", "DETECTED", "CONFIRMED", "READY", "ARMED", "VIRTUAL_FILLED",
    "CLOSED_TP", "CLOSED_SL", "EXPIRED", "INVALIDATED", "BLOCKED",
)
ACTION_TYPES = ("WAIT", "LIMIT", "MARKET")
ENTRY_TYPES = ("BUY_MARKET", "SELL_MARKET", "BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP")
TERMINAL_STATUSES = {"CLOSED_TP", "CLOSED_SL", "EXPIRED", "INVALIDATED", "BLOCKED"}
ACTIVE_RISK_STATUSES = {"ARMED", "VIRTUAL_FILLED"}

ALLOWED_TRANSITIONS = {
    "WATCH": {"DETECTED", "EXPIRED", "INVALIDATED", "BLOCKED"},
    "DETECTED": {"CONFIRMED", "EXPIRED", "INVALIDATED", "BLOCKED"},
    "CONFIRMED": {"READY", "EXPIRED", "INVALIDATED", "BLOCKED"},
    "READY": {"ARMED", "EXPIRED", "INVALIDATED", "BLOCKED"},
    "ARMED": {"VIRTUAL_FILLED", "EXPIRED", "INVALIDATED", "BLOCKED"},
    "VIRTUAL_FILLED": {"CLOSED_TP", "CLOSED_SL", "EXPIRED", "INVALIDATED"},
    "CLOSED_TP": set(), "CLOSED_SL": set(), "EXPIRED": set(),
    "INVALIDATED": set(), "BLOCKED": set(),
}

READY_PLAN_FIELDS = (
    "entry_type", "entry_price", "stop_loss", "take_profit", "rr_after_costs",
    "invalidation_rule", "confirmation_timestamp", "expiry_timestamp",
    "risk_allocation_pct",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_signal_id() -> str:
    return f"SIG-{uuid.uuid4().hex.upper()}"


def proposed_trade_idea_id(
    stream_key: tuple[str, str, str],
    direction: str,
    anchor_time: int,
    anchor_price: float,
    atr: float | None,
) -> str:
    bucket_size = max((atr or 0.0) * 0.25, 0.01)
    price_bucket = round(anchor_price / bucket_size)
    raw = "|".join(map(str, (*stream_key, direction, anchor_time, price_bucket)))
    return f"IDEA-{sha256(raw.encode()).hexdigest()[:20].upper()}"


def assert_transition(previous: str, current: str) -> None:
    if previous not in ALLOWED_TRANSITIONS or current not in SIGNAL_STATUSES:
        raise ValueError(f"unknown signal state: {previous} -> {current}")
    if current not in ALLOWED_TRANSITIONS[previous]:
        raise ValueError(f"invalid signal transition: {previous} -> {current}")


def validate_ready_contract(signal: dict[str, Any]) -> None:
    if signal.get("status") not in {"READY", "ARMED", "VIRTUAL_FILLED", "CLOSED_TP", "CLOSED_SL"}:
        return
    required = (
        "signal_id", "trade_idea_id", "method", "direction", "risk_decision",
        "explanation_vi",
    )
    missing = [field for field in required if signal.get(field) in (None, "")]
    plan = signal.get("entry_plan") or {}
    missing.extend(
        f"entry_plan.{field}" for field in READY_PLAN_FIELDS if plan.get(field) is None
    )
    if missing:
        raise ValueError(f"READY contract missing: {', '.join(missing)}")
    if signal["direction"] not in {"BUY", "SELL"}:
        raise ValueError("READY direction must be BUY or SELL")
    if plan["entry_type"] not in ENTRY_TYPES:
        raise ValueError("READY entry_type is invalid")
    if float(plan["rr_after_costs"]) < 2.0:
        raise ValueError("READY rr_after_costs must be >= 2.0")


def transition_path(final_status: str) -> list[str]:
    if final_status == "BLOCKED":
        return ["WATCH", "DETECTED", "BLOCKED"]
    if final_status == "DETECTED":
        return ["WATCH", "DETECTED"]
    if final_status == "CONFIRMED":
        return ["WATCH", "DETECTED", "CONFIRMED"]
    if final_status == "READY":
        return ["WATCH", "DETECTED", "CONFIRMED", "READY"]
    if final_status == "ARMED":
        return ["WATCH", "DETECTED", "CONFIRMED", "READY", "ARMED"]
    raise ValueError(f"unsupported creation status: {final_status}")
