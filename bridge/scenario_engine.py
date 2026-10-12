"""Observer-only Phase 1 scenarios. Never creates orders or performance statistics."""
from __future__ import annotations

from typing import Any

from bridge.signal_engine import MODEL_A, MODEL_B, MODEL_C, stale_safe_view


def _recent(event: dict[str, Any] | None, bars: list[dict[str, Any]], window: int = 3) -> bool:
    if not event or not bars:
        return False
    return int(event.get("time", -1)) in {int(bar["time"]) for bar in bars[-window:]}


def _volume_assessment(volume: dict[str, Any]) -> dict[str, Any]:
    rvol = volume.get("rvol20")
    if rvol is None:
        status, note = "NOT_AVAILABLE", "Chưa đủ 21 nến đóng để đánh giá RVOL20."
    elif volume.get("volume_spike"):
        status, note = "SPIKE", "Tick volume tăng mạnh so với trung bình 20 nến trước."
    elif volume.get("volume_dry_up"):
        status, note = "DRY_UP", "Tick volume suy giảm so với trung bình 20 nến trước."
    else:
        status, note = "NORMAL", "Tick volume nằm trong vùng bình thường của RVOL20."
    return {"source": "MT5_TICK_VOLUME", "status": status, "rvol20": rvol, "note_vi": note}


def _scenario(
    scenario_id: str,
    title: str,
    method: str,
    detected: bool,
    met: list[str],
    missing: list[str],
    explanation: str,
    volume: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": scenario_id,
        "title": title,
        "method": method,
        "status": "DETECTED" if detected else "WATCH",
        "met_conditions": met,
        "missing_conditions": [] if detected else missing,
        "tick_volume": _volume_assessment(volume),
        "explanation_vi": explanation,
        "recommendation": "CHỜ",
        "execution": "NONE",
    }


def build_phase1_scenarios(
    bars: list[dict[str, Any]], market: dict[str, Any], volume: dict[str, Any]
) -> list[dict[str, Any]]:
    """Always return four acceptance scenarios. DETECTED is observational, never an entry."""
    sweep = market["liquidity_sweep"].get("last_event")
    recent_sweep = _recent(sweep, bars)
    bullish_sweep = bool(recent_sweep and sweep.get("direction") == "BULLISH")
    bearish_sweep = bool(recent_sweep and sweep.get("direction") == "BEARISH")
    fvg_retest = market["fvg"].get("last_retest")
    recent_retest = _recent(fvg_retest, bars)
    structure_event = market["market_structure"].get("last_event")
    recent_structure = _recent(structure_event, bars)

    return [
        _scenario(
            "BUY", "BUY Scenario", "Liquidity Sweep + Rejection tăng", bullish_sweep,
            ["Quét xuống swing low 2–2 đã xác nhận", "Nến đóng trở lại trên mức thanh khoản"]
            if bullish_sweep else [],
            ["Cần sweep + rejection tăng trong 3 nến M5 đã đóng gần nhất"],
            "Đã quan sát sweep tăng gần đây; đây chưa phải tín hiệu vào lệnh."
            if bullish_sweep else "Chưa có sweep tăng hợp lệ trên nến M5 đã đóng.",
            volume,
        ),
        _scenario(
            "SELL", "SELL Scenario", "Liquidity Sweep + Rejection giảm", bearish_sweep,
            ["Quét lên swing high 2–2 đã xác nhận", "Nến đóng trở lại dưới mức thanh khoản"]
            if bearish_sweep else [],
            ["Cần sweep + rejection giảm trong 3 nến M5 đã đóng gần nhất"],
            "Đã quan sát sweep giảm gần đây; đây chưa phải tín hiệu vào lệnh."
            if bearish_sweep else "Chưa có sweep giảm hợp lệ trên nến M5 đã đóng.",
            volume,
        ),
        _scenario(
            "POI_RETEST", "POI Retest", "FVG 3 nến + retest", recent_retest,
            ["FVG được xác nhận sau khi nến thứ ba đóng", "Giá đã retest vùng FVG bằng nến đóng"]
            if recent_retest else [],
            ["Cần FVG hợp lệ và retest trong 3 nến M5 đã đóng gần nhất"],
            "Đã quan sát retest FVG gần đây; POI chỉ dùng để theo dõi."
            if recent_retest else "Chưa có retest FVG gần đây để đánh dấu POI.",
            volume,
        ),
        _scenario(
            "BREAKOUT_REVERSAL", "Breakout / Reversal", "BOS / CHOCH bằng giá đóng cửa",
            recent_structure,
            [f"{structure_event['type']} {structure_event['direction']} đã xác nhận bằng close"]
            if recent_structure else [],
            ["Cần BOS hoặc CHOCH trong 3 nến M5 đã đóng gần nhất"],
            "Cấu trúc vừa có sự kiện xác nhận; tiếp tục chờ Signal Engine Phase 2."
            if recent_structure else "Chưa có BOS/CHOCH mới bằng giá đóng cửa.",
            volume,
        ),
    ]


_STATUS_RANK = {
    "VIRTUAL_FILLED": 8, "ARMED": 7, "READY": 6, "CONFIRMED": 5,
    "DETECTED": 4, "WATCH": 3, "BLOCKED": 2, "INVALIDATED": 1,
    "EXPIRED": 0, "CLOSED_TP": 0, "CLOSED_SL": 0,
}


def _best(signals: list[dict[str, Any]]) -> dict[str, Any] | None:
    return max(
        signals,
        key=lambda signal: (
            _STATUS_RANK.get(signal.get("status"), -1),
            int(signal.get("quality_score") or 0),
            int(signal.get("confirmation_timestamp") or 0),
        ),
        default=None,
    )


def _scenario_from_signal(title: str, signal: dict[str, Any] | None, fallback: dict[str, Any]) -> dict[str, Any]:
    source = signal or fallback
    plan = source.get("entry_plan") or {}
    return {
        "title": title,
        "signal_id": source.get("signal_id"),
        "trade_idea_id": source.get("trade_idea_id"),
        "method": source.get("method"),
        "status": source.get("status", "WATCH"),
        "direction": source.get("direction", "NONE"),
        "action": source.get("action", "WAIT"),
        "entry": plan.get("entry_price"),
        "stop_loss": plan.get("stop_loss"),
        "take_profit": plan.get("take_profit"),
        "rr": plan.get("rr_after_costs"),
        "met_conditions": source.get("met_conditions") or [],
        "missing_conditions": source.get("missing_conditions") or [],
        "explanation_vi": source.get("explanation_vi"),
        "volume_assessment": source.get("volume_assessment"),
        "invalidation": plan.get("invalidation_rule"),
        "quality_score": source.get("quality_score"),
        "why_not_higher": source.get("why_not_higher") or [],
        "historical_win_rate": None,
        "sample_size": None,
        "expectancy": None,
    }


def compose_phase2_dashboard(
    signals: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    *,
    connected: bool,
    market_open: bool,
    current_spread: float | None = None,
) -> dict[str, Any]:
    visible = [
        stale_safe_view(signal, connected, market_open, current_spread) for signal in signals
    ]
    actionable = [
        signal for signal in visible
        if signal.get("status") not in {"EXPIRED", "INVALIDATED", "CLOSED_TP", "CLOSED_SL"}
    ]
    by_method = {observation["method"]: observation for observation in observations}
    generic = {
        "method": "NO_VALID_SETUP", "status": "WATCH", "direction": "NONE",
        "action": "WAIT", "met_conditions": [],
        "missing_conditions": ["Chưa có setup định lượng hợp lệ trên nến M5 đã đóng."],
        "explanation_vi": "Hệ thống giữ trạng thái chờ; không tạo tín hiệu để lấp chỗ trống.",
        "why_not_higher": ["Không có trigger mới."],
        "volume_assessment": None,
    }
    buy = _best([signal for signal in actionable if signal.get("direction") == "BUY"])
    sell = _best([signal for signal in actionable if signal.get("direction") == "SELL"])
    poi = _best([
        signal for signal in actionable
        if signal.get("method") in {MODEL_B, MODEL_C}
        and (signal.get("entry_plan") or {}).get("entry_type", "").endswith("_LIMIT")
    ])
    breakout = _best([
        signal for signal in actionable if signal.get("method") in {MODEL_B, MODEL_C}
    ])
    buy_fallback = next(
        (item for item in observations if item.get("direction") == "BUY"), generic
    )
    sell_fallback = next(
        (item for item in observations if item.get("direction") == "SELL"), generic
    )
    scenarios = [
        _scenario_from_signal("BEST BUY SCENARIO", buy, buy_fallback),
        _scenario_from_signal("BEST SELL SCENARIO", sell, sell_fallback),
        _scenario_from_signal("POI RETEST", poi, by_method.get(MODEL_C) or by_method.get(MODEL_B) or generic),
        _scenario_from_signal("BREAKOUT / REVERSAL", breakout, by_method.get(MODEL_B) or generic),
    ]
    primary = _best(actionable)
    if primary:
        plan = primary.get("entry_plan") or {}
        primary_recommendation = {
            "signal_id": primary.get("signal_id"),
            "trade_idea_id": primary.get("trade_idea_id"),
            "market_bias": primary.get("direction"),
            "primary_action": primary.get("action", "WAIT"),
            "setup_type": primary.get("method"),
            "status": primary.get("status"),
            "entry": plan.get("entry_price"),
            "stop_loss": plan.get("stop_loss"),
            "take_profit": plan.get("take_profit"),
            "rr": plan.get("rr_after_costs"),
            "quality_score": primary.get("quality_score"),
            "volume_status": (primary.get("volume_assessment") or {}).get("status"),
            "signal_age_bars": None,
            "risk_status": (primary.get("risk_decision") or {}).get("status"),
            "ai_reason_vi": primary.get("explanation_vi"),
            "why_not_higher": primary.get("why_not_higher") or [],
            "invalidation": plan.get("invalidation_rule"),
            "historical_win_rate": None,
        }
    else:
        primary_recommendation = {
            "signal_id": None, "trade_idea_id": None, "market_bias": "NEUTRAL",
            "primary_action": "WAIT", "setup_type": "NO_VALID_SETUP", "status": "WATCH",
            "entry": None, "stop_loss": None, "take_profit": None, "rr": None,
            "quality_score": None, "volume_status": None, "signal_age_bars": None,
            "risk_status": "NO_ACTIVE_RISK",
            "ai_reason_vi": "Chưa có setup Phase 2 hợp lệ; tiếp tục chờ nến M5 đóng.",
            "why_not_higher": ["Không có trigger định lượng mới."],
            "invalidation": None, "historical_win_rate": None,
        }
    return {
        "main_recommendation": primary_recommendation,
        "scenarios": scenarios,
        "signals": visible,
        "observations": observations,
        "risk_summary": {
            "active_trade_ideas": len({
                signal.get("trade_idea_id") for signal in signals
                if signal.get("status") in {"ARMED", "VIRTUAL_FILLED"}
            }),
            "allocated_risk_pct": sum({
                signal.get("trade_idea_id"): float(
                    (signal.get("risk_decision") or {}).get("suggested_risk_pct") or 0
                )
                for signal in signals if signal.get("status") in {"ARMED", "VIRTUAL_FILLED"}
            }.values()),
            "max_global_risk_pct": 2.0,
            "signal_only": True,
            "live_trading": False,
        },
    }
