import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from bridge.risk_engine import evaluate_risk
from bridge.signal_engine import (
    MODEL_A, MODEL_B, MODEL_C, build_phase2_candidates, stale_safe_view,
)
from bridge.signal_registry import (
    advance_virtual_lifecycle, create_signal_tables, record_new_signals, signal_history,
)
from bridge.signal_schema import assert_transition, transition_path, validate_ready_contract
from tests.fixtures_phase2 import (
    bar, fake_breakout, fvg_retest, high_spread, market_closed,
    model_a_buy, model_a_sell, model_b_buy, model_b_sell, model_c_buy,
    model_c_sell, sideways_bars, stale_data, uptrend_bars, downtrend_bars,
)

STREAM = ("Test Broker|Test-MT5", "XAUUSD", "M5")


def evaluate(fixture, active=None):
    bars, phase1, quote = fixture
    return build_phase2_candidates(bars, phase1, quote, STREAM, active or [])


class ModelATests(unittest.TestCase):
    def test_model_a_uses_live_ask_and_quantitative_rejection(self):
        bars, phase1, quote = model_a_buy()
        result = evaluate((bars, phase1, quote))
        signal = next(item for item in result["new_signals"] if item["method"] == MODEL_A)
        self.assertEqual("ARMED", signal["status"])
        self.assertEqual("MARKET", signal["action"])
        self.assertEqual(quote["ask"], signal["entry_plan"]["entry_price"])
        self.assertGreaterEqual(signal["entry_plan"]["rr_after_costs"], 2.0)
        self.assertEqual(quote["broker_time_msc"], signal["created_tick_msc"])
        self.assertIsNone(signal["historical_win_rate"])

    def test_model_a_blocks_stale_closed_and_high_spread(self):
        for fixture, expected in (
            (stale_data(), "DATA_STALE"),
            (market_closed(), "MARKET_CLOSED"),
            (high_spread(), "SPREAD_TOO_HIGH"),
        ):
            signal = next(item for item in evaluate(fixture)["new_signals"] if item["method"] == MODEL_A)
            self.assertEqual("BLOCKED", signal["status"])
            self.assertEqual("WAIT", signal["action"])
            self.assertIn(expected, signal["risk_decision"]["reasons"])

    def test_disconnected_view_never_recommends_new_market(self):
        signal = next(item for item in evaluate(model_a_buy())["new_signals"] if item["method"] == MODEL_A)
        view = stale_safe_view(signal, connected=False, market_open=True)
        self.assertEqual(("BLOCKED", "WAIT"), (view["status"], view["action"]))

    def test_high_spread_view_never_recommends_new_market(self):
        signal = next(item for item in evaluate(model_a_buy())["new_signals"] if item["method"] == MODEL_A)
        view = stale_safe_view(
            signal, connected=True, market_open=True,
            current_spread=float(signal["atr14"]) * 0.13,
        )
        self.assertEqual(("BLOCKED", "WAIT"), (view["status"], view["action"]))
        self.assertEqual("SPREAD_TOO_HIGH", view["display_block_reason"])

    def test_model_a_sell_is_symmetric(self):
        signal = next(
            item for item in evaluate(model_a_sell())["new_signals"] if item["method"] == MODEL_A
        )
        self.assertEqual(("SELL", "SELL_MARKET", "ARMED"), (
            signal["direction"], signal["entry_plan"]["entry_type"], signal["status"],
        ))


class ModelBTests(unittest.TestCase):
    def test_bos_continuation_is_limit_only(self):
        result = evaluate(model_b_buy())
        signal = next(item for item in result["new_signals"] if item["method"] == MODEL_B)
        self.assertEqual("ARMED", signal["status"])
        self.assertEqual("BUY_LIMIT", signal["entry_plan"]["entry_type"])
        self.assertEqual("LIMIT", signal["action"])
        self.assertGreaterEqual(signal["entry_plan"]["setup_geometry"]["range_atr"], 1.2)

    def test_fake_breakout_does_not_create_signal(self):
        result = evaluate(fake_breakout())
        self.assertFalse([item for item in result["new_signals"] if item["method"] == MODEL_B])

    def test_bos_sell_continuation_is_symmetric(self):
        signal = next(
            item for item in evaluate(model_b_sell())["new_signals"] if item["method"] == MODEL_B
        )
        self.assertEqual(("SELL", "SELL_LIMIT", "ARMED"), (
            signal["direction"], signal["entry_plan"]["entry_type"], signal["status"],
        ))


class ModelCTests(unittest.TestCase):
    def test_mss_fvg_ote_intersection_creates_limit(self):
        signal = next(
            item for item in evaluate(model_c_buy())["new_signals"] if item["method"] == MODEL_C
        )
        geometry = signal["entry_plan"]["setup_geometry"]
        self.assertEqual("ARMED", signal["status"])
        self.assertEqual("BUY_LIMIT", signal["entry_plan"]["entry_type"])
        self.assertLess(geometry["intersection_lower"], geometry["intersection_upper"])

    def test_ote_geometry_does_not_move_with_future_high(self):
        bars, phase1, quote = model_c_buy()
        before = next(
            item for item in evaluate((bars, phase1, quote))["new_signals"] if item["method"] == MODEL_C
        )["entry_plan"]["setup_geometry"]
        bars = list(bars) + [bar(30, 110, 140, 109, 130, 300)]
        quote = {**quote, "last_closed_bar": bars[-1]["time"], "broker_time": bars[-1]["time"] + 300,
                 "broker_time_msc": (bars[-1]["time"] + 300) * 1000}
        after = next(
            item for item in evaluate((bars, phase1, quote))["new_signals"] if item["method"] == MODEL_C
        )["entry_plan"]["setup_geometry"]
        for key in ("impulse_low", "impulse_high", "ote_lower", "ote_upper"):
            self.assertEqual(before[key], after[key])

    def test_mss_event_not_in_closed_bars_is_ignored(self):
        bars, phase1, quote = model_c_buy()
        phase1["market_structure"]["recent_events"][0]["time"] = bars[-1]["time"] + 300
        result = evaluate((bars, phase1, quote))
        self.assertFalse([item for item in result["new_signals"] if item["method"] == MODEL_C])

    def test_fvg_retest_fixture_is_explicit_not_market_data(self):
        bars, phase1, quote = fvg_retest()
        self.assertEqual("RETESTED", phase1["fvg"]["last_retest"]["status"])
        self.assertEqual(bars[-1]["time"], phase1["fvg"]["last_retest"]["time"])

    def test_mss_sell_fvg_ote_is_symmetric(self):
        signal = next(
            item for item in evaluate(model_c_sell())["new_signals"] if item["method"] == MODEL_C
        )
        self.assertEqual(("SELL", "SELL_LIMIT", "ARMED"), (
            signal["direction"], signal["entry_plan"]["entry_type"], signal["status"],
        ))


class RiskAndRegistryTests(unittest.TestCase):
    def test_status_machine_path_and_invalid_transition(self):
        self.assertEqual(
            ["WATCH", "DETECTED", "CONFIRMED", "READY", "ARMED"],
            transition_path("ARMED"),
        )
        assert_transition("WATCH", "DETECTED")
        with self.assertRaises(ValueError):
            assert_transition("WATCH", "READY")

    def test_ready_contract_rejects_incomplete_plan(self):
        with self.assertRaisesRegex(ValueError, "READY contract missing"):
            validate_ready_contract({
                "status": "READY", "signal_id": "SIG-X", "trade_idea_id": "IDEA-X",
                "method": MODEL_A, "direction": "BUY", "risk_decision": {"status": "PASS"},
                "explanation_vi": "test-only", "entry_plan": {"entry_price": 1.0},
            })

    def test_global_risk_cap_blocks_overlapping_exposure(self):
        decision = evaluate_risk(
            direction="BUY", entry=100, stop_loss=99, take_profit=103,
            spread=0.05, atr=2, data_fresh=True, market_open=True,
            active_global_risk_pct=1.8, duplicate_executable_idea=False,
        )
        self.assertEqual("BLOCKED", decision["status"])
        self.assertIn("GLOBAL_RISK_CAP_EXCEEDED", decision["reasons"])

    def test_same_trade_idea_cannot_arm_twice(self):
        first = next(item for item in evaluate(model_a_buy())["new_signals"] if item["method"] == MODEL_A)
        second = next(
            item for item in evaluate(model_a_buy(), [first])["new_signals"]
            if item["method"] == MODEL_A
        )
        self.assertEqual(first["trade_idea_id"], second["trade_idea_id"])
        self.assertEqual("BLOCKED", second["status"])
        self.assertIn("DUPLICATE_EXECUTABLE_TRADE_IDEA", second["risk_decision"]["reasons"])

    def test_signal_ids_are_unique(self):
        first = next(item for item in evaluate(model_a_buy())["new_signals"] if item["method"] == MODEL_A)
        second = next(item for item in evaluate(model_a_buy())["new_signals"] if item["method"] == MODEL_A)
        self.assertNotEqual(first["signal_id"], second["signal_id"])

    def test_snapshots_are_append_only_and_market_fill_uses_forward_tick(self):
        signal = next(item for item in evaluate(model_a_buy())["new_signals"] if item["method"] == MODEL_A)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "signals.sqlite3"

            def connect():
                con = sqlite3.connect(path)
                con.row_factory = sqlite3.Row
                return con

            with closing(connect()) as con, con:
                create_signal_tables(con)
            record_new_signals(connect, [signal], "2026-10-11T00:00:00+00:00")
            before = signal_history(connect, signal["signal_id"])
            armed_snapshot = json.dumps(before["snapshots"][-1]["payload"], sort_keys=True)
            _, _, quote = model_a_buy()
            forward_quote = {
                **quote,
                "broker_time_msc": quote["broker_time_msc"] + 1,
                "ask": quote["ask"] + 0.02,
                "bid": quote["bid"] + 0.02,
                "spread": quote["spread"],
            }
            changed = advance_virtual_lifecycle(
                connect, STREAM, forward_quote, "2026-10-11T00:00:01+00:00"
            )
            self.assertEqual("VIRTUAL_FILLED", changed[0]["status"])
            self.assertEqual(forward_quote["ask"], changed[0]["entry_plan"]["entry_price"])
            after = signal_history(connect, signal["signal_id"])
            self.assertEqual(armed_snapshot, json.dumps(after["snapshots"][-2]["payload"], sort_keys=True))
            self.assertEqual(len(before["snapshots"]) + 1, len(after["snapshots"]))

    def test_virtual_fill_waits_when_forward_spread_is_high(self):
        signal = next(item for item in evaluate(model_a_buy())["new_signals"] if item["method"] == MODEL_A)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "signals.sqlite3"

            def connect():
                con = sqlite3.connect(path)
                con.row_factory = sqlite3.Row
                return con

            with closing(connect()) as con, con:
                create_signal_tables(con)
            record_new_signals(connect, [signal], "2026-10-11T00:00:00+00:00")
            _, _, quote = model_a_buy()
            wide = {
                **quote, "broker_time_msc": quote["broker_time_msc"] + 1,
                "spread": float(signal["atr14"]) * 0.13,
                "market_open": True, "data_fresh": True,
            }
            self.assertEqual([], advance_virtual_lifecycle(
                connect, STREAM, wide, "2026-10-11T00:00:01+00:00"
            ))
            current = signal_history(connect, signal["signal_id"])["current"]
            self.assertEqual("ARMED", current["status"])


class DatasetCoverageTests(unittest.TestCase):
    def test_trend_fixtures_cover_both_directions(self):
        rising = uptrend_bars()
        falling = downtrend_bars()
        self.assertGreater(rising[-1]["close"], rising[0]["close"])
        self.assertLess(falling[-1]["close"], falling[0]["close"])

    def test_sideway_dataset_produces_no_fake_signal(self):
        bars = sideways_bars()
        phase1 = {
            "market_structure": {"bias": "NEUTRAL", "recent_events": [], "last_event": None},
            "liquidity_sweep": {"last_event": None, "recent_events": []},
            "fvg": {"recent_events": [], "last_event": None},
            "volume_intelligence": {
                "rvol20": 1, "session_rvol": 1, "volume_spike": False,
                "volume_dry_up": False, "volume_momentum": {"status": "STABLE"},
                "effort_vs_result": {"classification": "NORMAL"},
                "price_volume_divergence": {"status": "NONE"},
                "structure_volume_confirmation": {"status": "NEUTRAL"},
            },
        }
        quote = {
            "bid": 100, "ask": 100.1, "spread": 0.1,
            "broker_time": bars[-1]["time"] + 300,
            "broker_time_msc": (bars[-1]["time"] + 300) * 1000,
            "broker_utc_offset_seconds": 0, "last_closed_bar": bars[-1]["time"],
            "received_utc": "2026-10-11T00:00:00+00:00",
            "data_fresh": True, "market_open": True,
        }
        result = evaluate((bars, phase1, quote))
        self.assertEqual([], result["new_signals"])
        self.assertTrue(all(item["status"] == "WATCH" for item in result["observations"]))


if __name__ == "__main__":
    unittest.main()
