"""Deterministic synthetic Phase 2 fixtures. These are never market observations."""
from __future__ import annotations

from copy import deepcopy

BASE = 1_700_000_100


def bar(index, open_, high, low, close, volume=100):
    return {
        "time": BASE + index * 300,
        "open": open_, "high": high, "low": low, "close": close,
        "tick_volume": volume, "real_volume": 0, "spread": 10,
    }


def flat_bars(count=30, center=100.0):
    return [bar(i, center, center + 1, center - 1, center + 0.1, 100 + i % 5) for i in range(count)]


def uptrend_bars(count=30):
    return [
        bar(i, 100 + i * 0.2, 101 + i * 0.2, 99.5 + i * 0.2, 100.6 + i * 0.2, 100 + i)
        for i in range(count)
    ]


def downtrend_bars(count=30):
    return [
        bar(i, 110 - i * 0.2, 110.5 - i * 0.2, 109 - i * 0.2, 109.4 - i * 0.2, 100 + i)
        for i in range(count)
    ]


def sideways_bars(count=30):
    return [
        bar(i, 100, 101 + (i % 2) * 0.1, 99 - (i % 2) * 0.1, 100, 100)
        for i in range(count)
    ]


def volume(spike=False):
    return {
        "rvol20": 2.0 if spike else 1.0,
        "session_rvol": 1.4 if spike else 1.0,
        "volume_spike": spike,
        "volume_dry_up": False,
        "volume_momentum": {
            "ratio_vs_previous_closed_bar": 1.4 if spike else 1.0,
            "status": "RISING" if spike else "STABLE",
        },
        "effort_vs_result": {"classification": "EFFICIENT_EXPANSION" if spike else "NORMAL"},
        "price_volume_divergence": {"status": "NONE", "direction": None},
        "structure_volume_confirmation": {"status": "CONFIRMED" if spike else "NEUTRAL"},
    }


def phase1_base(bias="NEUTRAL", events=None, sweep=None, fvgs=None, spike=False):
    return {
        "market_structure": {
            "bias": bias, "recent_events": events or [],
            "last_event": (events or [None])[-1],
        },
        "liquidity_sweep": {"last_event": sweep, "recent_events": [sweep] if sweep else []},
        "fvg": {
            "last_event": (fvgs or [None])[-1], "recent_events": fvgs or [],
            "latest_active": (fvgs or [None])[-1], "last_retest": None,
        },
        "volume_intelligence": volume(spike),
    }


def quote(bars, bid, ask, *, stale=False, closed=False, tick_offset_ms=0):
    newest = bars[-1]["time"]
    return {
        "bid": bid, "ask": ask, "spread": ask - bid,
        "broker_time": newest + 300,
        "broker_time_msc": (newest + 300) * 1000 + tick_offset_ms,
        "broker_utc_offset_seconds": 0,
        "received_utc": "2026-10-11T00:00:00+00:00",
        "last_closed_bar": newest,
        "data_fresh": not stale,
        "market_open": not closed,
    }


def model_a_buy(spike=True):
    bars = flat_bars()
    bars[10] = bar(10, 100, 115, 99, 101, 150)
    bars[-1] = bar(29, 100.2, 101, 98.7, 100.7, 220)
    event = {
        "type": "LIQUIDITY_SWEEP_REJECTION", "direction": "BULLISH",
        "time": bars[-1]["time"], "level": 99.0, "level_time": bars[23]["time"],
        "level_confirmed_at": bars[25]["time"], "close": 100.7,
        "wick_extension": 0.3,
    }
    return bars, phase1_base(sweep=event, spike=spike), quote(bars, 100.7, 100.8)


def model_b_buy():
    bars = flat_bars(center=101)
    bars[10] = bar(10, 101, 112, 100, 102, 150)
    bars[23] = bar(23, 100, 101, 99.5, 100.5, 110)
    bars[25] = bar(25, 101, 103, 100, 102, 110)
    bars[-1] = bar(29, 102.5, 107, 102, 106.5, 240)
    event = {
        "type": "BOS", "direction": "BULLISH", "status": "CONFIRMED",
        "time": bars[-1]["time"], "close": 106.5, "level": 103.0,
        "level_time": bars[25]["time"], "level_confirmed_at": bars[27]["time"],
    }
    return bars, phase1_base(bias="BULLISH", events=[event], spike=True), quote(bars, 106.5, 106.6)


def model_c_buy():
    bars = flat_bars(center=102)
    bars[10] = bar(10, 102, 116, 101, 103, 160)
    bars[23] = bar(23, 101.5, 102.5, 100, 101.8, 120)
    bars[-1] = bar(29, 104, 110, 103.5, 109.5, 260)
    event = {
        "type": "CHOCH", "direction": "BULLISH", "status": "CONFIRMED",
        "time": bars[-1]["time"], "close": 109.5, "level": 104.0,
        "level_time": bars[20]["time"], "level_confirmed_at": bars[22]["time"],
    }
    gap = {
        "type": "FVG_3_CANDLE", "direction": "BULLISH", "status": "OPEN",
        "time": bars[-1]["time"], "lower": 102.5, "upper": 103.5,
        "confirmed_at_index": 29,
    }
    return bars, phase1_base(bias="BULLISH", events=[event], fvgs=[gap], spike=True), quote(bars, 109.5, 109.6)


def fake_breakout():
    bars, phase1, live_quote = model_b_buy()
    bars[-1] = bar(29, 102.5, 104, 101.5, 102.7, 90)
    phase1 = deepcopy(phase1)
    phase1["market_structure"]["recent_events"] = []
    phase1["market_structure"]["last_event"] = None
    return bars, phase1, quote(bars, 102.7, 102.8)


def fvg_retest():
    bars, phase1, live_quote = model_c_buy()
    bars.append(bar(30, 109, 109.5, 103.0, 105, 170))
    phase1 = deepcopy(phase1)
    phase1["fvg"]["last_retest"] = {
        "direction": "BULLISH", "time": bars[-1]["time"],
        "fvg_time": bars[-2]["time"], "status": "RETESTED",
    }
    return bars, phase1, quote(bars, 105, 105.1)


def high_spread():
    bars, phase1, live_quote = model_a_buy()
    return bars, phase1, quote(bars, 100.0, 101.5)


def stale_data():
    bars, phase1, live_quote = model_a_buy()
    return bars, phase1, quote(bars, 100.7, 100.8, stale=True)


def market_closed():
    bars, phase1, live_quote = model_a_buy()
    return bars, phase1, quote(bars, 100.7, 100.8, closed=True)


def _mirror_fixture(fixture):
    bars, phase1, live_quote = deepcopy(fixture)
    mirrored_bars = []
    for item in bars:
        mirrored_bars.append({
            **item,
            "open": 200 - item["open"],
            "high": 200 - item["low"],
            "low": 200 - item["high"],
            "close": 200 - item["close"],
        })
    phase1["market_structure"]["bias"] = {
        "BULLISH": "BEARISH", "BEARISH": "BULLISH",
    }.get(phase1["market_structure"]["bias"], "NEUTRAL")
    for event in phase1["market_structure"].get("recent_events") or []:
        event["direction"] = "BEARISH" if event["direction"] == "BULLISH" else "BULLISH"
        event["level"] = 200 - event["level"]
        event["close"] = 200 - event["close"]
    sweep = phase1["liquidity_sweep"].get("last_event")
    if sweep:
        sweep["direction"] = "BEARISH"
        sweep["level"] = 200 - sweep["level"]
        sweep["close"] = 200 - sweep["close"]
    for gap in phase1["fvg"].get("recent_events") or []:
        old_lower, old_upper = gap["lower"], gap["upper"]
        gap["direction"] = "BEARISH"
        gap["lower"], gap["upper"] = 200 - old_upper, 200 - old_lower
    mirrored_quote = {
        **live_quote,
        "bid": 200 - live_quote["ask"],
        "ask": 200 - live_quote["bid"],
    }
    mirrored_quote["spread"] = mirrored_quote["ask"] - mirrored_quote["bid"]
    return mirrored_bars, phase1, mirrored_quote


def model_a_sell():
    return _mirror_fixture(model_a_buy())


def model_b_sell():
    return _mirror_fixture(model_b_buy())


def model_c_sell():
    return _mirror_fixture(model_c_buy())
