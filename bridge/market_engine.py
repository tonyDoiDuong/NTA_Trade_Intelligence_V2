"""Deterministic Phase 1 market calculations using closed M5 bars only."""
from __future__ import annotations

from statistics import fmean
from typing import Any, Iterable


SWING_LEFT = 2
SWING_RIGHT = 2
ATR_PERIOD = 14


def ordered_bars(bars: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return unique bars sorted oldest-to-newest; last duplicate wins."""
    by_time = {int(bar["time"]): dict(bar) for bar in bars}
    return [by_time[timestamp] for timestamp in sorted(by_time)]


def true_ranges(bars: Iterable[dict[str, Any]]) -> list[float]:
    ordered = ordered_bars(bars)
    values: list[float] = []
    for index, bar in enumerate(ordered):
        high = float(bar["high"])
        low = float(bar["low"])
        if index == 0:
            values.append(high - low)
            continue
        previous_close = float(ordered[index - 1]["close"])
        values.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return values


def atr_wilder(bars: Iterable[dict[str, Any]], period: int = ATR_PERIOD) -> float | None:
    """Return Wilder ATR using only the supplied closed bars."""
    if period < 1:
        raise ValueError("period must be positive")
    ranges = true_ranges(bars)
    if len(ranges) < period:
        return None
    atr = fmean(ranges[:period])
    for value in ranges[period:]:
        atr = ((atr * (period - 1)) + value) / period
    return atr


def confirmed_swings(
    bars: Iterable[dict[str, Any]],
    left: int = SWING_LEFT,
    right: int = SWING_RIGHT,
) -> list[dict[str, Any]]:
    """Find strict fractal pivots known only after ``right`` later bars close."""
    ordered = ordered_bars(bars)
    if left < 1 or right < 1:
        raise ValueError("left and right must be positive")
    swings: list[dict[str, Any]] = []
    for index in range(left, len(ordered) - right):
        bar = ordered[index]
        neighbours = ordered[index - left : index] + ordered[index + 1 : index + right + 1]
        if all(float(bar["high"]) > float(other["high"]) for other in neighbours):
            swings.append(_swing("HIGH", index, bar, ordered[index + right], index + right))
        if all(float(bar["low"]) < float(other["low"]) for other in neighbours):
            swings.append(_swing("LOW", index, bar, ordered[index + right], index + right))
    return sorted(swings, key=lambda swing: (swing["confirmed_at_index"], swing["kind"]))


def _swing(
    kind: str,
    index: int,
    bar: dict[str, Any],
    confirmation_bar: dict[str, Any],
    confirmation_index: int,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "index": index,
        "time": int(bar["time"]),
        "price": float(bar["high"] if kind == "HIGH" else bar["low"]),
        "volume": int(bar["tick_volume"]),
        "confirmed_at": int(confirmation_bar["time"]),
        "confirmed_at_index": confirmation_index,
    }


def _sequence(swings: list[dict[str, Any]], kind: str) -> str:
    selected = [swing for swing in swings if swing["kind"] == kind]
    if len(selected) < 2:
        return "N/A"
    previous, latest = selected[-2:]
    if kind == "HIGH":
        return "HH" if latest["price"] > previous["price"] else "LH"
    return "HL" if latest["price"] > previous["price"] else "LL"


def _public_swing(swing: dict[str, Any]) -> dict[str, Any]:
    return {
        "time": swing["time"],
        "price": swing["price"],
        "confirmed_at": swing["confirmed_at"],
    }


def _structure_event(
    event_type: str,
    direction: str,
    break_bar: dict[str, Any],
    level: dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": event_type,
        "direction": direction,
        "status": "CONFIRMED",
        "time": int(break_bar["time"]),
        "close": float(break_bar["close"]),
        "level": float(level["price"]),
        "level_time": int(level["time"]),
        "level_confirmed_at": int(level["confirmed_at"]),
        "rule": "closed candle close beyond a previously confirmed swing",
    }


def analyze_market_structure(bars: Iterable[dict[str, Any]]) -> dict[str, Any]:
    ordered = ordered_bars(bars)
    base: dict[str, Any] = {
        "status": "INSUFFICIENT_DATA" if len(ordered) < 5 else "READY",
        "uses_closed_bars_only": True,
        "bar_count": len(ordered),
        "swing_rule": {"left": SWING_LEFT, "right": SWING_RIGHT, "strict": True},
        "bias": "NEUTRAL",
        "high_sequence": "N/A",
        "low_sequence": "N/A",
        "latest_swing_high": None,
        "latest_swing_low": None,
        "last_event": None,
        "recent_events": [],
        "invalidation_level": None,
    }
    if len(ordered) < 5:
        return base

    swings = confirmed_swings(ordered)
    highs = [swing for swing in swings if swing["kind"] == "HIGH"]
    lows = [swing for swing in swings if swing["kind"] == "LOW"]
    base["high_sequence"] = _sequence(swings, "HIGH")
    base["low_sequence"] = _sequence(swings, "LOW")
    if highs:
        base["latest_swing_high"] = _public_swing(highs[-1])
    if lows:
        base["latest_swing_low"] = _public_swing(lows[-1])

    confirmations: dict[int, list[dict[str, Any]]] = {}
    for swing in swings:
        confirmations.setdefault(int(swing["confirmed_at_index"]), []).append(swing)

    available_highs: list[dict[str, Any]] = []
    available_lows: list[dict[str, Any]] = []
    broken: set[tuple[str, int]] = set()
    events: list[dict[str, Any]] = []
    bias = "NEUTRAL"
    for index, bar in enumerate(ordered):
        for swing in confirmations.get(index, []):
            (available_highs if swing["kind"] == "HIGH" else available_lows).append(swing)

        if bias == "NEUTRAL" and len(available_highs) >= 2 and len(available_lows) >= 2:
            high_up = available_highs[-1]["price"] > available_highs[-2]["price"]
            low_up = available_lows[-1]["price"] > available_lows[-2]["price"]
            high_down = available_highs[-1]["price"] < available_highs[-2]["price"]
            low_down = available_lows[-1]["price"] < available_lows[-2]["price"]
            if high_up and low_up:
                bias = "BULLISH"
            elif high_down and low_down:
                bias = "BEARISH"

        close = float(bar["close"])
        crossed_highs = [
            swing
            for swing in available_highs
            if ("HIGH", swing["index"]) not in broken and close > float(swing["price"])
        ]
        crossed_lows = [
            swing
            for swing in available_lows
            if ("LOW", swing["index"]) not in broken and close < float(swing["price"])
        ]
        if crossed_highs:
            # One close can cross several prior levels. Mark every crossed level now
            # and report the highest one so an older level cannot emit a late BOS.
            active_high = max(crossed_highs, key=lambda swing: float(swing["price"]))
            event_type = "CHOCH" if bias == "BEARISH" else "BOS"
            events.append(_structure_event(event_type, "BULLISH", bar, active_high))
            broken.update(("HIGH", swing["index"]) for swing in crossed_highs)
            bias = "BULLISH"
        elif crossed_lows:
            active_low = min(crossed_lows, key=lambda swing: float(swing["price"]))
            event_type = "CHOCH" if bias == "BULLISH" else "BOS"
            events.append(_structure_event(event_type, "BEARISH", bar, active_low))
            broken.update(("LOW", swing["index"]) for swing in crossed_lows)
            bias = "BEARISH"

    base["bias"] = bias
    base["recent_events"] = events[-10:]
    base["last_event"] = events[-1] if events else None
    if bias == "BULLISH" and lows:
        base["invalidation_level"] = lows[-1]["price"]
    elif bias == "BEARISH" and highs:
        base["invalidation_level"] = highs[-1]["price"]
    return base


def detect_liquidity_sweeps(bars: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Detect wick sweep + directional close back through a confirmed swing level."""
    ordered = ordered_bars(bars)
    swings = confirmed_swings(ordered) if len(ordered) >= 5 else []
    confirmations: dict[int, list[dict[str, Any]]] = {}
    for swing in swings:
        confirmations.setdefault(int(swing["confirmed_at_index"]), []).append(swing)

    available_highs: list[dict[str, Any]] = []
    available_lows: list[dict[str, Any]] = []
    consumed: set[tuple[str, int]] = set()
    events: list[dict[str, Any]] = []
    for index, bar in enumerate(ordered):
        for swing in confirmations.get(index, []):
            (available_highs if swing["kind"] == "HIGH" else available_lows).append(swing)
        active_high = next(
            (s for s in reversed(available_highs) if ("HIGH", s["index"]) not in consumed),
            None,
        )
        active_low = next(
            (s for s in reversed(available_lows) if ("LOW", s["index"]) not in consumed),
            None,
        )
        open_price = float(bar["open"])
        close = float(bar["close"])
        high = float(bar["high"])
        low = float(bar["low"])
        if active_high and high > active_high["price"] and close < active_high["price"] and close < open_price:
            events.append(_sweep_event("BEARISH", bar, active_high, high - active_high["price"]))
            consumed.add(("HIGH", active_high["index"]))
        elif active_low and low < active_low["price"] and close > active_low["price"] and close > open_price:
            events.append(_sweep_event("BULLISH", bar, active_low, active_low["price"] - low))
            consumed.add(("LOW", active_low["index"]))
        else:
            # A close through a level is a structural break, not a later sweep candidate.
            consumed.update(
                ("HIGH", swing["index"])
                for swing in available_highs
                if close > float(swing["price"])
            )
            consumed.update(
                ("LOW", swing["index"])
                for swing in available_lows
                if close < float(swing["price"])
            )

    return {
        "status": "READY" if len(ordered) >= 5 else "INSUFFICIENT_DATA",
        "rule": "wick beyond confirmed 2-2 swing, close back inside, directional candle",
        "last_event": events[-1] if events else None,
        "recent_events": events[-10:],
    }


def _sweep_event(
    direction: str,
    bar: dict[str, Any],
    level: dict[str, Any],
    extension: float,
) -> dict[str, Any]:
    return {
        "type": "LIQUIDITY_SWEEP_REJECTION",
        "direction": direction,
        "status": "DETECTED",
        "time": int(bar["time"]),
        "level": float(level["price"]),
        "level_time": int(level["time"]),
        "level_confirmed_at": int(level["confirmed_at"]),
        "close": float(bar["close"]),
        "wick_extension": float(extension),
    }


def detect_fvg(bars: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Detect three-candle wick imbalance and later closed-bar retests/fills."""
    ordered = ordered_bars(bars)
    gaps: list[dict[str, Any]] = []
    for index in range(2, len(ordered)):
        first = ordered[index - 2]
        third = ordered[index]
        if float(third["low"]) > float(first["high"]):
            gaps.append(
                _fvg_record("BULLISH", index, third, float(first["high"]), float(third["low"]))
            )
        elif float(third["high"]) < float(first["low"]):
            gaps.append(
                _fvg_record("BEARISH", index, third, float(third["high"]), float(first["low"]))
            )

    last_retest = None
    for gap in gaps:
        for bar in ordered[int(gap["confirmed_at_index"]) + 1 :]:
            if gap["direction"] == "BULLISH" and float(bar["low"]) <= gap["upper"]:
                gap["last_retest_time"] = int(bar["time"])
                gap["status"] = "FILLED" if float(bar["low"]) <= gap["lower"] else "RETESTED"
                last_retest = {
                    "direction": gap["direction"],
                    "time": int(bar["time"]),
                    "fvg_time": gap["time"],
                    "status": gap["status"],
                }
                if gap["status"] == "FILLED":
                    break
            elif gap["direction"] == "BEARISH" and float(bar["high"]) >= gap["lower"]:
                gap["last_retest_time"] = int(bar["time"])
                gap["status"] = "FILLED" if float(bar["high"]) >= gap["upper"] else "RETESTED"
                last_retest = {
                    "direction": gap["direction"],
                    "time": int(bar["time"]),
                    "fvg_time": gap["time"],
                    "status": gap["status"],
                }
                if gap["status"] == "FILLED":
                    break

    active = [gap for gap in gaps if gap["status"] != "FILLED"]
    return {
        "status": "READY" if len(ordered) >= 3 else "INSUFFICIENT_DATA",
        "rule": "bullish when candle 3 low > candle 1 high; bearish when candle 3 high < candle 1 low",
        "last_event": gaps[-1] if gaps else None,
        "latest_active": active[-1] if active else None,
        "last_retest": last_retest,
        "recent_events": gaps[-10:],
    }


def _fvg_record(
    direction: str,
    index: int,
    confirmation_bar: dict[str, Any],
    lower: float,
    upper: float,
) -> dict[str, Any]:
    return {
        "type": "FVG_3_CANDLE",
        "direction": direction,
        "status": "OPEN",
        "time": int(confirmation_bar["time"]),
        "confirmed_at_index": index,
        "lower": lower,
        "upper": upper,
        "size": upper - lower,
        "last_retest_time": None,
    }


def analyze_market(bars: Iterable[dict[str, Any]]) -> dict[str, Any]:
    ordered = ordered_bars(bars)
    atr = atr_wilder(ordered)
    return {
        "market_structure": analyze_market_structure(ordered),
        "liquidity_sweep": detect_liquidity_sweeps(ordered),
        "fvg": detect_fvg(ordered),
        "atr14": {
            "status": "READY" if atr is not None else "INSUFFICIENT_DATA",
            "period": ATR_PERIOD,
            "method": "WILDER",
            "value": round(atr, 6) if atr is not None else None,
            "uses_closed_bars_only": True,
        },
    }
