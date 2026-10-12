"""Phase 2 models A-C and entry planning from closed M5 bars plus the current quote."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bridge.market_engine import atr_wilder, confirmed_swings, ordered_bars, true_ranges
from bridge.risk_engine import DEFAULT_RISK_CONFIG, calculate_rr, evaluate_risk
from bridge.signal_schema import (
    ACTIVE_RISK_STATUSES, new_signal_id, proposed_trade_idea_id,
    transition_path, validate_ready_contract,
)

MODEL_A = "MODEL_A_LIQUIDITY_SWEEP_REJECTION"
MODEL_B = "MODEL_B_BOS_CONTINUATION_RETEST"
MODEL_C = "MODEL_C_MSS_FVG_OTE"
MODEL_ORDER = {MODEL_A: 0, MODEL_B: 1, MODEL_C: 2}


def _bar_index(bars: list[dict[str, Any]], timestamp: int) -> int | None:
    return next((index for index, bar in enumerate(bars) if int(bar["time"]) == timestamp), None)


def _bar_range(bar: dict[str, Any]) -> float:
    return float(bar["high"]) - float(bar["low"])


def _body_ratio(bar: dict[str, Any]) -> float:
    candle_range = _bar_range(bar)
    return abs(float(bar["close"]) - float(bar["open"])) / candle_range if candle_range > 0 else 0.0


def _atr_at(bars: list[dict[str, Any]], index: int) -> float | None:
    return atr_wilder(bars[: index + 1])


def _is_displacement(bars: list[dict[str, Any]], index: int, atr: float | None) -> tuple[bool, dict[str, float]]:
    candle_range = _bar_range(bars[index])
    range_atr = candle_range / atr if atr and atr > 0 else 0.0
    body_ratio = _body_ratio(bars[index])
    return range_atr >= 1.2 and body_ratio >= 0.6, {
        "range_atr": round(range_atr, 4),
        "body_ratio": round(body_ratio, 4),
    }


def _protective_swing(
    bars: list[dict[str, Any]], event_index: int, direction: str
) -> dict[str, Any] | None:
    kind = "LOW" if direction == "BUY" else "HIGH"
    eligible = [
        swing for swing in confirmed_swings(bars[: event_index + 1])
        if swing["kind"] == kind and int(swing["confirmed_at_index"]) <= event_index
    ]
    return eligible[-1] if eligible else None


def _structural_target(
    bars: list[dict[str, Any]],
    direction: str,
    entry: float,
    stop_loss: float,
    spread: float,
) -> tuple[float | None, float | None]:
    kind = "HIGH" if direction == "BUY" else "LOW"
    candidates = sorted(
        {
            float(swing["price"])
            for swing in confirmed_swings(bars)
            if swing["kind"] == kind
            and ((direction == "BUY" and float(swing["price"]) > entry)
                 or (direction == "SELL" and float(swing["price"]) < entry))
        },
        reverse=direction == "SELL",
    )
    for target in candidates:
        rr = calculate_rr(direction, entry, stop_loss, target, spread)["rr_after_costs"]
        if rr is not None and rr >= DEFAULT_RISK_CONFIG.minimum_rr:
            return target, float(rr)
    return None, None


def _volume_assessment(volume: dict[str, Any], direction: str) -> dict[str, Any]:
    momentum = volume.get("volume_momentum") or {}
    divergence = volume.get("price_volume_divergence") or {}
    structure_confirmation = volume.get("structure_volume_confirmation") or {}
    opposing = (
        divergence.get("status") == "DETECTED"
        and ((direction == "BUY" and divergence.get("direction") == "BEARISH")
             or (direction == "SELL" and divergence.get("direction") == "BULLISH"))
    )
    if volume.get("volume_dry_up") or opposing:
        status = "WARNING"
        reason = "Tick volume suy yếu hoặc có phân kỳ bất lợi."
    elif volume.get("volume_spike") or structure_confirmation.get("status") == "CONFIRMED":
        status = "CONFIRM"
        reason = "Tick volume xác nhận mức tham gia cao hơn nền gần đây."
    else:
        status = "NEUTRAL"
        reason = "Tick volume không phủ nhận nhưng cũng chưa xác nhận mạnh setup."
    return {
        "status": status,
        "source": "MT5_TICK_VOLUME",
        "rvol20": volume.get("rvol20"),
        "session_rvol": volume.get("session_rvol"),
        "volume_momentum": momentum,
        "volume_spike": volume.get("volume_spike"),
        "volume_dry_up": volume.get("volume_dry_up"),
        "effort_vs_result": volume.get("effort_vs_result"),
        "price_volume_divergence": divergence,
        "explanation_vi": reason,
    }


def _quality_score(met: list[str], missing: list[str], volume_status: str) -> int:
    total = max(len(met) + len(missing), 1)
    score = round(100 * len(met) / total)
    score += 5 if volume_status == "CONFIRM" else -5 if volume_status == "WARNING" else 0
    return max(0, min(100, score))


def is_market_open(quote: dict[str, Any]) -> bool:
    if "market_open" in quote:
        return bool(quote["market_open"])
    broker_time = int(quote.get("broker_time") or 0)
    offset = quote.get("broker_utc_offset_seconds")
    if not broker_time or offset is None:
        return False
    utc_time = datetime.fromtimestamp(broker_time - int(offset), timezone.utc)
    return utc_time.weekday() < 5


def _quote_context(quote: dict[str, Any]) -> dict[str, Any]:
    return {
        "data_fresh": bool(quote.get("data_fresh", True)),
        "market_open": is_market_open(quote),
        "spread": float(quote.get("spread") or (float(quote["ask"]) - float(quote["bid"]))),
        "tick_msc": int(quote.get("broker_time_msc") or int(quote.get("broker_time", 0)) * 1000),
    }


def _observation(method: str, title: str, direction: str = "NONE") -> dict[str, Any]:
    return {
        "method": method, "title": title, "direction": direction,
        "status": "WATCH", "action": "WAIT", "entry_plan": None,
        "met_conditions": [], "missing_conditions": [],
        "explanation_vi": "Đang chờ sự kiện mới trên nến M5 đã đóng.",
        "why_not_higher": ["Chưa có trigger định lượng mới ở nến đóng gần nhất."],
        "historical_win_rate": None,
    }


def _model_a(
    bars: list[dict[str, Any]], phase1: dict[str, Any], quote: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    observation = _observation(MODEL_A, "Liquidity Sweep + Rejection")
    event = (phase1.get("liquidity_sweep") or {}).get("last_event")
    if not event or not bars or int(event["time"]) != int(bars[-1]["time"]):
        observation["missing_conditions"] = ["Cần sweep một swing 2–2 đã xác nhận tại nến đóng mới nhất."]
        return observation, None
    direction = "BUY" if event["direction"] == "BULLISH" else "SELL"
    observation["direction"] = direction
    index = len(bars) - 1
    bar = bars[index]
    atr = _atr_at(bars, index)
    candle_range = _bar_range(bar)
    depth_atr = float(event["wick_extension"]) / atr if atr and atr > 0 else None
    if direction == "BUY":
        wick = min(float(bar["open"]), float(bar["close"])) - float(bar["low"])
        close_location = (float(bar["close"]) - float(bar["low"])) / candle_range if candle_range > 0 else 0
        entry = float(quote["ask"])
        sweep_extreme = float(bar["low"])
    else:
        wick = float(bar["high"]) - max(float(bar["open"]), float(bar["close"]))
        close_location = (float(bar["high"]) - float(bar["close"])) / candle_range if candle_range > 0 else 0
        entry = float(quote["bid"])
        sweep_extreme = float(bar["high"])
    wick_ratio = wick / candle_range if candle_range > 0 else 0
    checks = [
        ("Swing 2–2 đã xác nhận trước khi bị quét", int(event["level_confirmed_at"]) <= int(event["time"])),
        ("Độ sâu sweep nằm trong 0.05–0.75 ATR14", depth_atr is not None and 0.05 <= depth_atr <= 0.75),
        ("Râu rejection chiếm ít nhất 40% biên độ nến", wick_ratio >= 0.40),
        ("Close nằm ít nhất 60% từ cực trị sweep", close_location >= 0.60),
    ]
    met = [label for label, passed in checks if passed]
    missing = [label for label, passed in checks if not passed]
    buffer = max(float(quote.get("spread", 0)) * 1.5, (atr or 0) * 0.05)
    stop = sweep_extreme - buffer if direction == "BUY" else sweep_extreme + buffer
    target, _ = _structural_target(bars, direction, entry, stop, float(quote.get("spread", 0)))
    if target is None:
        missing.append("Chưa có mục tiêu cấu trúc hợp lệ cho R:R sau chi phí từ 2.0.")
    plan = {
        "entry_type": f"{direction}_MARKET", "action": "MARKET",
        "entry_price": round(entry, 6), "stop_loss": round(stop, 6),
        "take_profit": round(target, 6) if target is not None else None,
        "invalidation_price": round(stop, 6),
        "invalidation_rule": f"{direction} invalid khi giá live xuyên stop bảo vệ sweep.",
        "confirmation_timestamp": int(event["time"]),
        "expiry_timestamp": int(event["time"]) + 900,
        "setup_geometry": {
            "sweep_level": event["level"], "sweep_extreme": sweep_extreme,
            "depth_atr": round(depth_atr, 4) if depth_atr is not None else None,
            "wick_ratio": round(wick_ratio, 4), "close_location": round(close_location, 4),
        },
    }
    volume = _volume_assessment(phase1["volume_intelligence"], direction)
    observation.update({
        "status": "DETECTED", "met_conditions": met, "missing_conditions": missing,
        "volume_assessment": volume,
        "explanation_vi": "Nến mới nhất đã quét thanh khoản và đóng trở lại vùng hợp lệ; MARKET chỉ được phép sau Risk Gate.",
        "why_not_higher": missing + ([] if volume["status"] == "CONFIRM" else [volume["explanation_vi"]]),
    })
    proposal = {
        **observation, "entry_plan": plan, "atr14": atr,
        "anchor_time": int(event["level_time"]), "anchor_price": float(event["level"]),
        "source_bar_time": int(event["time"]),
        "confirmed": all(passed for _, passed in checks),
    }
    return observation, proposal


def _recent_structure_event(
    phase1: dict[str, Any], bars: list[dict[str, Any]], event_type: str
) -> dict[str, Any] | None:
    recent_times = {int(bar["time"]) for bar in bars[-7:]}
    events = (phase1.get("market_structure") or {}).get("recent_events") or []
    eligible = [
        event for event in events
        if event.get("type") == event_type and int(event.get("time", -1)) in recent_times
    ]
    return eligible[-1] if eligible else None


def _related_fvg(
    phase1: dict[str, Any], direction: str, event_time: int
) -> dict[str, Any] | None:
    events = (phase1.get("fvg") or {}).get("recent_events") or []
    eligible = [
        gap for gap in events
        if gap.get("direction") == direction and abs(int(gap["time"]) - event_time) <= 600
    ]
    return eligible[-1] if eligible else None


def _model_b(
    bars: list[dict[str, Any]], phase1: dict[str, Any], quote: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    observation = _observation(MODEL_B, "BOS Continuation + Retest")
    event = _recent_structure_event(phase1, bars, "BOS")
    if not event:
        observation["missing_conditions"] = ["Cần BOS mới trong tối đa 7 nến M5 đã đóng."]
        return observation, None
    event_index = _bar_index(bars, int(event["time"]))
    if event_index is None:
        return observation, None
    direction = "BUY" if event["direction"] == "BULLISH" else "SELL"
    atr = _atr_at(bars, event_index)
    displacement, metrics = _is_displacement(bars, event_index, atr)
    bias_ok = phase1["market_structure"].get("bias") == ("BULLISH" if direction == "BUY" else "BEARISH")
    gap = _related_fvg(phase1, event["direction"], int(event["time"]))
    if gap and gap.get("status") != "FILLED":
        zone_lower, zone_upper = float(gap["lower"]), float(gap["upper"])
        zone_source = "RELATED_FVG"
    else:
        width = (atr or 0) * 0.05
        zone_lower, zone_upper = float(event["level"]) - width, float(event["level"]) + width
        zone_source = "BOS_LEVEL"
    current = bars[-1]
    touches = float(current["low"]) <= zone_upper and float(current["high"]) >= zone_lower
    is_current_break = int(event["time"]) == int(current["time"])
    if not is_current_break and not touches:
        observation.update({
            "direction": direction,
            "missing_conditions": ["Đang chờ giá retest vùng BOS/FVG đã xác nhận."],
            "explanation_vi": "BOS đã có nhưng nến mới nhất chưa retest vùng entry.",
        })
        return observation, None
    entry = (zone_lower + zone_upper) / 2
    protective = _protective_swing(bars, event_index, direction)
    buffer = max(float(quote.get("spread", 0)) * 1.5, (atr or 0) * 0.05)
    stop = None
    if protective:
        stop = (
            float(protective["price"]) - buffer if direction == "BUY"
            else float(protective["price"]) + buffer
        )
    target, _ = (
        _structural_target(bars, direction, entry, stop, float(quote.get("spread", 0)))
        if stop is not None else (None, None)
    )
    quote_side_ok = float(quote["ask"]) > entry if direction == "BUY" else float(quote["bid"]) < entry
    not_expired = int(current["time"]) <= int(event["time"]) + 2100
    checks = [
        ("Bias cấu trúc cùng hướng BOS", bias_ok),
        ("Breakout candle có range ≥ 1.2 ATR và body ≥ 60%", displacement),
        ("Vùng retest lấy từ BOS level hoặc FVG liên quan", zone_upper > zone_lower),
        ("LIMIT nằm đúng phía so với quote live", quote_side_ok),
        ("Setup chưa hết hạn 7 nến", not_expired),
        ("Có swing bảo vệ cho Stop Loss", protective is not None),
        ("Có mục tiêu cấu trúc đạt R:R sau chi phí từ 2.0", target is not None),
    ]
    met = [label for label, passed in checks if passed]
    missing = [label for label, passed in checks if not passed]
    plan = {
        "entry_type": f"{direction}_LIMIT", "action": "LIMIT",
        "entry_price": round(entry, 6),
        "stop_loss": round(stop, 6) if stop is not None else None,
        "take_profit": round(target, 6) if target is not None else None,
        "invalidation_price": round(stop, 6) if stop is not None else None,
        "invalidation_rule": f"{direction} invalid khi giá xuyên swing bảo vệ trước khi/ sau khi virtual fill.",
        "confirmation_timestamp": int(event["time"]),
        "expiry_timestamp": int(event["time"]) + 2100,
        "setup_geometry": {
            "zone_lower": round(zone_lower, 6), "zone_upper": round(zone_upper, 6),
            "zone_source": zone_source, **metrics,
        },
    }
    volume = _volume_assessment(phase1["volume_intelligence"], direction)
    observation.update({
        "direction": direction, "status": "DETECTED",
        "met_conditions": met, "missing_conditions": missing,
        "volume_assessment": volume,
        "explanation_vi": "BOS có displacement; kế hoạch chỉ dùng LIMIT tại vùng retest, không đuổi MARKET.",
        "why_not_higher": missing + ([] if volume["status"] == "CONFIRM" else [volume["explanation_vi"]]),
    })
    proposal = {
        **observation, "entry_plan": plan, "atr14": atr,
        "anchor_time": int(event["level_time"]), "anchor_price": float(event["level"]),
        "source_bar_time": int(current["time"]),
        "confirmed": all(passed for _, passed in checks[:-1]),
    }
    return observation, proposal


def _model_c(
    bars: list[dict[str, Any]], phase1: dict[str, Any], quote: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    observation = _observation(MODEL_C, "MSS + FVG + OTE")
    event = _recent_structure_event(phase1, bars, "CHOCH")
    if not event:
        observation["missing_conditions"] = ["Cần MSS/CHOCH mới được xác nhận bằng close M5."]
        return observation, None
    event_index = _bar_index(bars, int(event["time"]))
    if event_index is None:
        return observation, None
    direction = "BUY" if event["direction"] == "BULLISH" else "SELL"
    atr = _atr_at(bars, event_index)
    displacement, metrics = _is_displacement(bars, event_index, atr)
    protective = _protective_swing(bars, event_index, direction)
    gap = _related_fvg(phase1, event["direction"], int(event["time"]))
    if protective:
        if direction == "BUY":
            impulse_low, impulse_high = float(protective["price"]), float(bars[event_index]["high"])
            ote_lower = impulse_high - 0.79 * (impulse_high - impulse_low)
            ote_upper = impulse_high - 0.618 * (impulse_high - impulse_low)
        else:
            impulse_high, impulse_low = float(protective["price"]), float(bars[event_index]["low"])
            ote_lower = impulse_low + 0.618 * (impulse_high - impulse_low)
            ote_upper = impulse_low + 0.79 * (impulse_high - impulse_low)
    else:
        impulse_low = impulse_high = ote_lower = ote_upper = None
    intersection_lower = intersection_upper = None
    if gap and ote_lower is not None and gap.get("status") != "FILLED":
        intersection_lower = max(float(gap["lower"]), float(ote_lower))
        intersection_upper = min(float(gap["upper"]), float(ote_upper))
        if intersection_lower >= intersection_upper:
            intersection_lower = intersection_upper = None
    entry = (
        (intersection_lower + intersection_upper) / 2
        if intersection_lower is not None and intersection_upper is not None else None
    )
    buffer = max(float(quote.get("spread", 0)) * 1.5, (atr or 0) * 0.05)
    stop = None
    if protective:
        stop = (
            float(protective["price"]) - buffer if direction == "BUY"
            else float(protective["price"]) + buffer
        )
    target, _ = (
        _structural_target(bars, direction, entry, stop, float(quote.get("spread", 0)))
        if entry is not None and stop is not None else (None, None)
    )
    quote_side_ok = (
        entry is not None
        and (float(quote["ask"]) > entry if direction == "BUY" else float(quote["bid"]) < entry)
    )
    checks = [
        ("MSS/CHOCH xác nhận bằng close M5", True),
        ("MSS candle có displacement", displacement),
        ("Có FVG cùng hướng liên quan và chưa filled", gap is not None and gap.get("status") != "FILLED"),
        ("Impulse origin dùng swing đã xác nhận tại thời điểm MSS", protective is not None),
        ("FVG giao vùng OTE 61.8–79%", intersection_lower is not None),
        ("LIMIT nằm đúng phía so với quote live", quote_side_ok),
        ("Có mục tiêu cấu trúc đạt R:R sau chi phí từ 2.0", target is not None),
    ]
    met = [label for label, passed in checks if passed]
    missing = [label for label, passed in checks if not passed]
    plan = {
        "entry_type": f"{direction}_LIMIT", "action": "LIMIT",
        "entry_price": round(entry, 6) if entry is not None else None,
        "stop_loss": round(stop, 6) if stop is not None else None,
        "take_profit": round(target, 6) if target is not None else None,
        "invalidation_price": round(stop, 6) if stop is not None else None,
        "invalidation_rule": f"{direction} invalid khi giá xuyên swing gốc của impulse.",
        "confirmation_timestamp": int(event["time"]),
        "expiry_timestamp": int(event["time"]) + 2700,
        "setup_geometry": {
            "impulse_low": impulse_low, "impulse_high": impulse_high,
            "ote_lower": ote_lower, "ote_upper": ote_upper,
            "fvg_lower": float(gap["lower"]) if gap else None,
            "fvg_upper": float(gap["upper"]) if gap else None,
            "intersection_lower": intersection_lower,
            "intersection_upper": intersection_upper,
            **metrics,
        },
    }
    volume = _volume_assessment(phase1["volume_intelligence"], direction)
    observation.update({
        "direction": direction, "status": "DETECTED",
        "met_conditions": met, "missing_conditions": missing,
        "volume_assessment": volume,
        "explanation_vi": "MSS, displacement, FVG và OTE được tính tại thời điểm xác nhận; impulse không kéo lại bằng dữ liệu tương lai.",
        "why_not_higher": missing + ([] if volume["status"] == "CONFIRM" else [volume["explanation_vi"]]),
    })
    proposal = {
        **observation, "entry_plan": plan, "atr14": atr,
        "anchor_time": int(event["level_time"]), "anchor_price": float(event["level"]),
        "source_bar_time": int(bars[-1]["time"]),
        "confirmed": all(passed for _, passed in checks[:-1]),
    }
    return observation, proposal


def _active_risk(signals: list[dict[str, Any]]) -> float:
    by_idea: dict[str, float] = {}
    for signal in signals:
        if signal.get("status") not in ACTIVE_RISK_STATUSES:
            continue
        idea = signal.get("trade_idea_id")
        risk = float((signal.get("risk_decision") or {}).get("suggested_risk_pct") or 0)
        if idea:
            by_idea[idea] = max(by_idea.get(idea, 0), risk)
    return sum(by_idea.values())


def _link_trade_idea(
    proposal: dict[str, Any],
    stream_key: tuple[str, str, str],
    signals: list[dict[str, Any]],
) -> str:
    entry = (proposal.get("entry_plan") or {}).get("entry_price")
    threshold = max(float(proposal.get("atr14") or 0) * 0.25, 0.01)
    if entry is not None:
        for signal in signals:
            existing_entry = (signal.get("entry_plan") or {}).get("entry_price")
            if (
                signal.get("direction") == proposal.get("direction")
                and existing_entry is not None
                and abs(float(existing_entry) - float(entry)) <= threshold
            ):
                return str(signal["trade_idea_id"])
    return proposed_trade_idea_id(
        stream_key, proposal["direction"], proposal["anchor_time"],
        proposal["anchor_price"], proposal.get("atr14"),
    )


def build_phase2_candidates(
    bars: list[dict[str, Any]],
    phase1: dict[str, Any],
    quote: dict[str, Any],
    stream_key: tuple[str, str, str],
    active_signals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ordered = ordered_bars(bars)
    active = list(active_signals or [])
    observations: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    for evaluator in (_model_a, _model_b, _model_c):
        observation, proposal = evaluator(ordered, phase1, quote)
        observations.append(observation)
        if proposal:
            proposals.append(proposal)
    proposals.sort(key=lambda item: (-_quality_score(
        item["met_conditions"], item["missing_conditions"],
        item["volume_assessment"]["status"],
    ), MODEL_ORDER[item["method"]]))
    context = _quote_context(quote)
    created: list[dict[str, Any]] = []
    for proposal in proposals:
        all_known = active + created
        idea_id = _link_trade_idea(proposal, stream_key, all_known)
        duplicate = any(
            signal.get("trade_idea_id") == idea_id
            and signal.get("status") in ACTIVE_RISK_STATUSES
            for signal in all_known
        )
        plan = proposal["entry_plan"]
        risk = evaluate_risk(
            direction=proposal["direction"],
            entry=plan.get("entry_price"), stop_loss=plan.get("stop_loss"),
            take_profit=plan.get("take_profit"), spread=context["spread"],
            atr=proposal.get("atr14"), data_fresh=context["data_fresh"],
            market_open=context["market_open"], active_global_risk_pct=_active_risk(all_known),
            duplicate_executable_idea=duplicate,
        )
        if not proposal["confirmed"]:
            final_status = "DETECTED"
            action = "WAIT"
        elif risk["status"] == "PASS":
            final_status = "ARMED"
            action = plan["action"]
        else:
            final_status = "BLOCKED"
            action = "WAIT"
        if plan.get("entry_price") is not None and plan.get("stop_loss") is not None and plan.get("take_profit") is not None:
            plan.update(calculate_rr(
                proposal["direction"], plan["entry_price"], plan["stop_loss"],
                plan["take_profit"], context["spread"],
            ))
        plan["risk_allocation_pct"] = risk["suggested_risk_pct"] if risk["status"] == "PASS" else 0.0
        signal = {
            "signal_id": new_signal_id(), "trade_idea_id": idea_id,
            "method": proposal["method"], "title": proposal["title"],
            "direction": proposal["direction"], "status": final_status, "action": action,
            "entry_plan": plan, "risk_decision": risk,
            "met_conditions": proposal["met_conditions"],
            "missing_conditions": proposal["missing_conditions"],
            "volume_assessment": proposal["volume_assessment"],
            "quality_score": _quality_score(
                proposal["met_conditions"], proposal["missing_conditions"],
                proposal["volume_assessment"]["status"],
            ),
            "explanation_vi": proposal["explanation_vi"],
            "why_not_higher": proposal["why_not_higher"] + [
                reason for reason in risk["reasons"] if reason not in proposal["why_not_higher"]
            ],
            "historical_win_rate": None, "sample_size": None, "expectancy": None,
            "stream": {
                "broker_key": stream_key[0], "symbol": stream_key[1], "timeframe": stream_key[2],
            },
            "source_bar_time": proposal["source_bar_time"],
            "atr14": proposal.get("atr14"),
            "confirmation_timestamp": plan["confirmation_timestamp"],
            "expiry_timestamp": plan["expiry_timestamp"],
            "created_tick_msc": context["tick_msc"],
            "created_utc": quote.get("received_utc"),
            "state_path": transition_path(final_status),
            "execution_enabled": False,
        }
        validate_ready_contract(signal)
        created.append(signal)
    created.sort(key=lambda item: MODEL_ORDER[item["method"]])
    return {"new_signals": created, "observations": observations}


def stale_safe_view(
    signal: dict[str, Any],
    connected: bool,
    market_open: bool,
    current_spread: float | None = None,
) -> dict[str, Any]:
    """Return a presentation copy that never recommends a new action on stale/closed data."""
    view = {**signal, "entry_plan": dict(signal.get("entry_plan") or {})}
    atr = signal.get("atr14")
    spread_high = (
        current_spread is not None and atr is not None and float(atr) > 0
        and float(current_spread) / float(atr) > DEFAULT_RISK_CONFIG.max_spread_atr_ratio
    )
    if signal.get("status") in {"READY", "ARMED"} and (
        not connected or not market_open or spread_high
    ):
        view["status"] = "BLOCKED"
        view["action"] = "WAIT"
        reason = (
            "DATA_STALE" if not connected else
            "MARKET_CLOSED" if not market_open else
            "SPREAD_TOO_HIGH"
        )
        view["display_block_reason"] = reason
        view["why_not_higher"] = list(view.get("why_not_higher") or []) + [reason]
    return view
