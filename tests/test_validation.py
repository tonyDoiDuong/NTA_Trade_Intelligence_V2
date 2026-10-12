import os
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

os.environ.setdefault("NTA_DB_PATH", str(Path(tempfile.gettempdir()) / "nta_phase1_import.sqlite3"))
from bridge.server import TickPayload, _decorate_tick  # noqa: E402

BASE = 1_700_000_100


def bars(count=300):
    return [
        {
            "time": BASE + index * 300,
            "open": 2650.0, "high": 2651.0, "low": 2649.5, "close": 2650.5,
            "tick_volume": 100 + index % 7, "real_volume": 0, "spread": 20,
        }
        for index in range(count)
    ]


def payload(count=300):
    history = bars(count)
    newest = history[-1]["time"]
    return {
        "source": "MT5", "symbol": "XAUUSD", "timeframe": "M5",
        "broker": "Test Broker", "broker_server": "Test-MT5",
        "bid": 2650.1, "ask": 2650.3, "spread": 0.2,
        "tick_volume_current": 10, "tick_volume_closed": 100,
        "last_closed_bar": newest, "broker_time": newest + 300,
        "broker_time_msc": (newest + 300) * 1000,
        "broker_utc_offset_seconds": 7200, "ea_version": "bridge-0.4",
        "m5_bars": history,
    }


class PayloadValidationTests(unittest.TestCase):
    def test_valid_300_and_350_closed_bar_payload(self):
        self.assertEqual(300, len(TickPayload.model_validate(payload(300)).m5_bars))
        self.assertEqual(350, len(TickPayload.model_validate(payload(350)).m5_bars))

    def test_rejects_less_than_300_or_more_than_350(self):
        with self.assertRaises(ValidationError):
            TickPayload.model_validate(payload(299))
        with self.assertRaises(ValidationError):
            TickPayload.model_validate(payload(351))

    def test_rejects_live_or_future_bar(self):
        data = payload()
        data["broker_time"] = data["last_closed_bar"] + 100
        data["broker_time_msc"] = data["broker_time"] * 1000
        with self.assertRaises(ValidationError):
            TickPayload.model_validate(data)

    def test_rejects_wrong_order_duplicate_and_unaligned_time(self):
        data = payload()
        data["m5_bars"][0], data["m5_bars"][1] = data["m5_bars"][1], data["m5_bars"][0]
        with self.assertRaises(ValidationError):
            TickPayload.model_validate(data)
        data = payload()
        data["m5_bars"][1]["time"] += 1
        with self.assertRaises(ValidationError):
            TickPayload.model_validate(data)

    def test_rejects_invalid_ohlc_and_spread(self):
        data = payload()
        data["m5_bars"][0]["high"] = 2640.0
        with self.assertRaises(ValidationError):
            TickPayload.model_validate(data)
        data = payload()
        data["spread"] = 2.0
        with self.assertRaises(ValidationError):
            TickPayload.model_validate(data)

    def test_legacy_tick_without_dimensions_is_accepted_only_without_bars(self):
        data = payload()
        data.pop("broker")
        data.pop("broker_server")
        data.pop("broker_utc_offset_seconds")
        data["m5_bars"] = []
        self.assertEqual("UNKNOWN_LEGACY", TickPayload.model_validate(data).stream_key()[0])
        data["m5_bars"] = payload()["m5_bars"]
        with self.assertRaises(ValidationError):
            TickPayload.model_validate(data)

    def test_timestamp_conversion_is_explicit_and_legacy_is_unknown(self):
        data = payload()
        record = TickPayload.model_validate(data).model_dump(exclude={"m5_bars"})
        record["received_utc"] = "2026-10-11T00:00:00+00:00"
        decorated = _decorate_tick(record)
        self.assertEqual("CURRENT_OFFSET_AT_RECEIPT", decorated["timezone_status"])
        self.assertIsNotNone(decorated["broker_time_utc"])
        record["broker_utc_offset_seconds"] = None
        decorated = _decorate_tick(record)
        self.assertEqual("UNKNOWN_LEGACY_PAYLOAD", decorated["timezone_status"])
        self.assertIsNone(decorated["broker_time_utc"])


if __name__ == "__main__":
    unittest.main()
