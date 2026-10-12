"""Closed-bar-only Phase 1 volume analytics and orchestration."""
from __future__ import annotations

from statistics import fmean
from typing import Any, Iterable

from bridge.market_engine import analyze_market, confirmed_swings, ordered_bars, true_ranges
from bridge.scenario_engine import build_phase1_scenarios

RVOL_LOOKBACK = 20
SESSION_MIN_SAMPLES = 5


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def _session_name(utc_seconds: int) -> str:
    hour = (utc_seconds // 3600) % 24
    if 0 <= hour < 8:
        return "ASIA"
    if 8 <= hour < 13:
        return "LONDON"
    if 13 <= hour < 22:
        return "NEW_YORK"
    return "OFF_HOURS"


def _rvol_at(bars: list[dict[str, Any]], index: int) -> float | None:
    if index < RVOL_LOOKBACK:
        return None
    baseline = fmean(float(bar["tick_volume"]) for bar in bars[index - RVOL_LOOKBACK:index])
    if baseline <= 0:
        return None
    return float(bars[index]["tick_volume"]) / baseline


def analyze_volume(
    bars: Iterable[dict[str, Any]],
    structure: dict[str, Any] | None = None,
    broker_utc_offset_seconds: int = 0,
) -> dict[str, Any]:
    ordered = ordered_bars(bars)
    result: dict[str, Any] = {
        "status": "INSUFFICIENT_DATA" if len(ordered) < RVOL_LOOKBACK + 1 else "READY",
        "uses_closed_bars_only": True,
        "volume_source": "MT5_TICK_VOLUME",
        "volume_note": "Số tick do broker MT5 cung cấp; không phải khối lượng giao dịch tập trung.",
        "bar_count": len(ordered),
        "rvol20": None,
        "session": None,
        "session_rvol": None,
        "session_sample_size": 0,
        "volume_spike": None,
        "volume_dry_up": None,
        "volume_momentum": {
            "ratio_vs_previous_closed_bar": None,
            "status": "NOT_AVAILABLE",
        },
        "thresholds": {"spike_rvol_gte": 1.8, "dry_up_rvol_lte": 0.6},
        "price_volume_divergence": {"status": "NOT_AVAILABLE", "direction": None},
        "effort_vs_result": {
            "effort_ratio": None,
            "result_ratio": None,
            "classification": "NOT_AVAILABLE",
        },
        "structure_volume_confirmation": {
            "status": "NOT_AVAILABLE",
            "event_time": None,
            "rvol20_at_event": None,
        },
    }
    if not ordered:
        return result

    latest = ordered[-1]
    if len(ordered) >= 2 and float(ordered[-2]["tick_volume"]) > 0:
        momentum_ratio = float(latest["tick_volume"]) / float(ordered[-2]["tick_volume"])
        momentum_status = (
            "RISING" if momentum_ratio >= 1.2
            else "FALLING" if momentum_ratio <= 0.8
            else "STABLE"
        )
        result["volume_momentum"] = {
            "ratio_vs_previous_closed_bar": _round(momentum_ratio),
            "status": momentum_status,
        }
    latest_utc = int(latest["time"]) - int(broker_utc_offset_seconds)
    session = _session_name(latest_utc)
    result["session"] = session
    prior_same_session = [
        float(bar["tick_volume"])
        for bar in ordered[:-1]
        if _session_name(int(bar["time"]) - int(broker_utc_offset_seconds)) == session
    ][-60:]
    result["session_sample_size"] = len(prior_same_session)
    if len(prior_same_session) >= SESSION_MIN_SAMPLES and fmean(prior_same_session) > 0:
        result["session_rvol"] = _round(float(latest["tick_volume"]) / fmean(prior_same_session))

    if len(ordered) >= RVOL_LOOKBACK + 1:
        rvol = _rvol_at(ordered, len(ordered) - 1)
        result["rvol20"] = _round(rvol)
        if rvol is not None:
            result["volume_spike"] = rvol >= 1.8
            result["volume_dry_up"] = rvol <= 0.6
        ranges = true_ranges(ordered)
        prior_ranges = ranges[-(RVOL_LOOKBACK + 1):-1]
        mean_range = fmean(prior_ranges)
        result_ratio = ranges[-1] / mean_range if mean_range > 0 else None
        classification = "NOT_AVAILABLE" if rvol is None or result_ratio is None else "NORMAL"
        if rvol is not None and result_ratio is not None:
            if rvol >= 1.5 and result_ratio <= 0.8:
                classification = "ABSORPTION"
            elif rvol >= 1.2 and result_ratio >= 1.2:
                classification = "EFFICIENT_EXPANSION"
            elif rvol <= 0.6 and result_ratio <= 0.8:
                classification = "LOW_PARTICIPATION"
        result["effort_vs_result"] = {
            "effort_ratio": _round(rvol),
            "result_ratio": _round(result_ratio),
            "classification": classification,
        }

    result["price_volume_divergence"] = _volume_divergence(
        confirmed_swings(ordered) if len(ordered) >= 5 else []
    )
    if structure and structure.get("last_event"):
        event_time = int(structure["last_event"]["time"])
        event_index = next(
            (index for index, bar in enumerate(ordered) if int(bar["time"]) == event_time),
            None,
        )
        event_rvol = _rvol_at(ordered, event_index) if event_index is not None else None
        status = "NOT_AVAILABLE"
        if event_rvol is not None:
            status = "CONFIRMED" if event_rvol >= 1.2 else "WEAK" if event_rvol <= 0.8 else "NEUTRAL"
        result["structure_volume_confirmation"] = {
            "status": status,
            "event_time": event_time,
            "rvol20_at_event": _round(event_rvol),
        }
    return result


def _volume_divergence(swings: list[dict[str, Any]]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for kind, direction, divergent in (
        ("HIGH", "BEARISH", lambda newest, previous: newest > previous),
        ("LOW", "BULLISH", lambda newest, previous: newest < previous),
    ):
        selected = [swing for swing in swings if swing["kind"] == kind]
        if len(selected) < 2:
            continue
        previous, latest = selected[-2:]
        if divergent(latest["price"], previous["price"]) and latest["volume"] < previous["volume"] * 0.9:
            candidates.append({
                "status": "DETECTED",
                "direction": direction,
                "time": latest["time"],
                "confirmed_at": latest["confirmed_at"],
                "price_change_pct": _round((latest["price"] / previous["price"] - 1) * 100),
                "volume_change_pct": _round((latest["volume"] / previous["volume"] - 1) * 100),
            })
    return max(candidates, key=lambda item: int(item["time"])) if candidates else {
        "status": "NONE", "direction": None
    }


def analyze_phase1(
    bars: Iterable[dict[str, Any]], broker_utc_offset_seconds: int = 0
) -> dict[str, Any]:
    ordered = ordered_bars(bars)
    market = analyze_market(ordered)
    volume = analyze_volume(ordered, market["market_structure"], broker_utc_offset_seconds)
    return {
        **market,
        "volume_intelligence": volume,
        "scenarios": build_phase1_scenarios(ordered, market, volume),
    }
