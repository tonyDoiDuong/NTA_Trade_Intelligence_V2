import unittest

from bridge.analytics import analyze_phase1, analyze_volume
from bridge.market_engine import (
    analyze_market_structure, atr_wilder, confirmed_swings, detect_fvg,
    detect_liquidity_sweeps,
)

BASE = 1_700_000_100  # M5-aligned synthetic fixture; never market data.


def bar(index, open_, high, low, close, volume=100):
    return {
        "time": BASE + index * 300,
        "open": open_, "high": high, "low": low, "close": close,
        "tick_volume": volume, "real_volume": 0, "spread": 10,
    }


class MarketStructureTests(unittest.TestCase):
    def setUp(self):
        self.bars = [
            bar(0, 8, 10, 7, 9), bar(1, 9, 12, 8, 11),
            bar(2, 11, 15, 9, 14), bar(3, 14, 14.5, 10, 11),
            bar(4, 11, 12, 8, 9), bar(5, 9, 13, 9, 12),
            bar(6, 12, 14, 10, 13), bar(7, 13, 16, 12, 15.5, 150),
        ]

    def test_fractal_two_two_is_confirmed_two_bars_later(self):
        high = next(s for s in confirmed_swings(self.bars) if s["kind"] == "HIGH")
        self.assertEqual(2, high["index"])
        self.assertEqual(self.bars[4]["time"], high["confirmed_at"])

    def test_strict_fractal_rejects_equal_high(self):
        bars = [bar(i, 9, h, 8, 9) for i, h in enumerate([10, 12, 15, 15, 11])]
        self.assertFalse([s for s in confirmed_swings(bars) if s["kind"] == "HIGH"])

    def test_closed_candle_break_confirms_bullish_bos(self):
        result = analyze_market_structure(self.bars)
        self.assertEqual(
            ("BOS", "BULLISH", 15.0),
            (result["last_event"]["type"], result["last_event"]["direction"], result["last_event"]["level"]),
        )

    def test_no_repaint_when_future_bars_are_appended(self):
        before = analyze_market_structure(self.bars)["last_event"]
        future = self.bars + [
            bar(8, 15.5, 16.2, 13.5, 14), bar(9, 14, 15, 12, 13),
            bar(10, 13, 14, 7, 7.5, 180),
        ]
        after = analyze_market_structure(future)["recent_events"]
        self.assertEqual([before], [event for event in after if event["time"] == before["time"]])
        self.assertEqual(("CHOCH", "BEARISH"), (after[-1]["type"], after[-1]["direction"]))

    def test_one_close_does_not_emit_old_bos_later(self):
        bars = [
            bar(0, 9, 10, 8, 9), bar(1, 9, 12, 8.5, 11),
            bar(2, 11, 11.5, 9, 10), bar(3, 10, 13, 9.5, 12),
            bar(4, 12, 11, 8, 9), bar(5, 9, 14, 9, 13),
            bar(6, 13, 13.5, 10, 12), bar(7, 12, 20, 11, 19),
            bar(8, 19, 19.5, 18, 19),
        ]
        event_times = [event["time"] for event in analyze_market_structure(bars)["recent_events"]]
        self.assertEqual(len(event_times), len(set(event_times)))


class PatternTests(unittest.TestCase):
    def test_bullish_liquidity_sweep_rejection_uses_confirmed_low(self):
        bars = [
            bar(0, 10, 11, 9, 10), bar(1, 10, 10.5, 8, 9),
            bar(2, 9, 10, 5, 8), bar(3, 8, 10, 7, 9),
            bar(4, 9, 11, 8, 10), bar(5, 5.5, 10, 4, 6),
        ]
        event = detect_liquidity_sweeps(bars)["last_event"]
        self.assertEqual(
            ("BULLISH", 5.0, bars[5]["time"]),
            (event["direction"], event["level"], event["time"]),
        )

    def test_three_candle_fvg_bullish_and_bearish(self):
        bullish = [bar(0, 9, 10, 8, 9), bar(1, 9, 12, 9, 11), bar(2, 11, 13, 11, 12)]
        bearish = [bar(0, 12, 13, 11, 12), bar(1, 12, 12, 8, 9), bar(2, 9, 10, 7, 8)]
        self.assertEqual("BULLISH", detect_fvg(bullish)["last_event"]["direction"])
        self.assertEqual("BEARISH", detect_fvg(bearish)["last_event"]["direction"])

    def test_wilder_atr14_known_constant_range(self):
        self.assertEqual(2.0, atr_wilder([bar(i, 100, 101, 99, 100) for i in range(20)]))


class VolumeTests(unittest.TestCase):
    def test_rvol_spike_and_effort_result(self):
        bars = [bar(i, 100, 100.5, 99.5, 100.1, 100) for i in range(20)]
        result = analyze_volume(bars + [bar(20, 100, 100.25, 99.75, 100.05, 200)])
        self.assertEqual(2.0, result["rvol20"])
        self.assertTrue(result["volume_spike"])
        self.assertFalse(result["volume_dry_up"])
        self.assertEqual("ABSORPTION", result["effort_vs_result"]["classification"])

    def test_rvol_dry_up_and_boundaries(self):
        base = [bar(i, 100, 101, 99, 100, 100) for i in range(20)]
        self.assertTrue(analyze_volume(base + [bar(20, 100, 101, 99, 100, 60)])["volume_dry_up"])
        self.assertTrue(analyze_volume(base + [bar(20, 100, 101, 99, 100, 180)])["volume_spike"])

    def test_zero_baseline_is_not_falsely_classified(self):
        bars = [bar(i, 100, 101, 99, 100, 0) for i in range(20)]
        result = analyze_volume(bars + [bar(20, 100, 101, 99, 100, 100)])
        self.assertIsNone(result["rvol20"])
        self.assertIsNone(result["volume_spike"])
        self.assertIsNone(result["volume_dry_up"])

    def test_phase1_always_returns_four_scenarios(self):
        result = analyze_phase1([bar(i, 100, 101, 99, 100) for i in range(21)])
        self.assertEqual(
            ["BUY", "SELL", "POI_RETEST", "BREAKOUT_REVERSAL"],
            [scenario["id"] for scenario in result["scenarios"]],
        )
        self.assertTrue(all(s["status"] in {"WATCH", "DETECTED"} for s in result["scenarios"]))
        self.assertTrue(all(s["tick_volume"]["source"] == "MT5_TICK_VOLUME" for s in result["scenarios"]))


if __name__ == "__main__":
    unittest.main()
