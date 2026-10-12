"""Deterministic Phase 2 risk gates; produces advice only and never sizes by assumption."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RiskConfig:
    max_risk_per_setup_pct: float = 2.0
    default_suggested_risk_pct: float = 0.5
    max_global_risk_pct: float = 2.0
    minimum_rr: float = 2.0
    max_spread_atr_ratio: float = 0.12
    signal_only: bool = True
    live_trading: bool = False


DEFAULT_RISK_CONFIG = RiskConfig()


def calculate_rr(
    direction: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    spread: float,
) -> dict[str, float | None]:
    if direction == "BUY":
        raw_risk, raw_reward = entry - stop_loss, take_profit - entry
    elif direction == "SELL":
        raw_risk, raw_reward = stop_loss - entry, entry - take_profit
    else:
        raise ValueError("direction must be BUY or SELL")
    if raw_risk <= 0 or raw_reward <= 0:
        return {
            "risk_distance": raw_risk, "reward_distance": raw_reward,
            "estimated_cost": max(spread, 0.0), "rr_gross": None, "rr_after_costs": None,
        }
    cost = max(spread, 0.0)
    rr_gross = raw_reward / raw_risk
    effective_risk = raw_risk + cost
    effective_reward = raw_reward - cost
    rr_after_costs = effective_reward / effective_risk if effective_reward > 0 else None
    return {
        "risk_distance": round(raw_risk, 6),
        "reward_distance": round(raw_reward, 6),
        "estimated_cost": round(cost, 6),
        "rr_gross": round(rr_gross, 4),
        "rr_after_costs": round(rr_after_costs, 4) if rr_after_costs is not None else None,
    }


def evaluate_risk(
    *,
    direction: str,
    entry: float | None,
    stop_loss: float | None,
    take_profit: float | None,
    spread: float,
    atr: float | None,
    data_fresh: bool,
    market_open: bool,
    active_global_risk_pct: float,
    duplicate_executable_idea: bool,
    requested_risk_pct: float | None = None,
    config: RiskConfig = DEFAULT_RISK_CONFIG,
) -> dict[str, Any]:
    suggested = (
        config.default_suggested_risk_pct
        if requested_risk_pct is None else float(requested_risk_pct)
    )
    reasons: list[str] = []
    rr = {
        "risk_distance": None, "reward_distance": None, "estimated_cost": spread,
        "rr_gross": None, "rr_after_costs": None,
    }
    if None not in (entry, stop_loss, take_profit):
        rr = calculate_rr(direction, float(entry), float(stop_loss), float(take_profit), spread)
    if not data_fresh:
        reasons.append("DATA_STALE")
    if not market_open:
        reasons.append("MARKET_CLOSED")
    if atr is None or atr <= 0:
        reasons.append("ATR_NOT_AVAILABLE")
        spread_atr_ratio = None
    else:
        spread_atr_ratio = spread / atr
        if spread_atr_ratio > config.max_spread_atr_ratio:
            reasons.append("SPREAD_TOO_HIGH")
    if None in (entry, stop_loss, take_profit):
        reasons.append("ENTRY_PLAN_INCOMPLETE")
    if rr["rr_after_costs"] is None or float(rr["rr_after_costs"]) < config.minimum_rr:
        reasons.append("RR_BELOW_MINIMUM")
    if suggested <= 0 or suggested > config.max_risk_per_setup_pct:
        reasons.append("SETUP_RISK_EXCEEDS_CAP")
    if duplicate_executable_idea:
        reasons.append("DUPLICATE_EXECUTABLE_TRADE_IDEA")
    global_after = active_global_risk_pct + (0.0 if duplicate_executable_idea else suggested)
    if global_after > config.max_global_risk_pct:
        reasons.append("GLOBAL_RISK_CAP_EXCEEDED")
    return {
        "status": "PASS" if not reasons else "BLOCKED",
        "reasons": reasons,
        "suggested_risk_pct": suggested,
        "max_risk_per_setup_pct": config.max_risk_per_setup_pct,
        "active_global_risk_pct": round(active_global_risk_pct, 4),
        "global_risk_after_pct": round(global_after, 4),
        "max_global_risk_pct": config.max_global_risk_pct,
        "spread_atr_ratio": round(spread_atr_ratio, 4) if spread_atr_ratio is not None else None,
        "risk_money": None,
        "lot_size": None,
        "sizing_status": "N/A_ACCOUNT_OR_VERIFIED_CONTRACT_DATA_MISSING",
        "signal_only": config.signal_only,
        "live_trading": config.live_trading,
        **rr,
        "config": asdict(config),
    }
