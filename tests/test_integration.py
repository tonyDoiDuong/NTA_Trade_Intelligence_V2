import json
import asyncio
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

os.environ.setdefault("NTA_DB_PATH", str(Path(tempfile.gettempdir()) / "nta_phase1_import.sqlite3"))
from bridge import server  # noqa: E402

BASE = 1_700_000_100


def sample_bars(count=320, shift=0.0):
    result = []
    for index in range(count):
        center = 2600.0 + shift + (index % 12) * 0.1
        result.append({
            "time": BASE + index * 300,
            "open": center, "high": center + 0.8, "low": center - 0.8,
            "close": center + (0.2 if index % 2 else -0.2),
            "tick_volume": 90 + index % 30, "real_volume": 0, "spread": 20,
        })
    return result


def sample_payload(broker, server_name, shift=0.0):
    history = sample_bars(320, shift)
    newest = history[-1]["time"]
    return {
        "source": "MT5", "symbol": "XAUUSD", "timeframe": "M5",
        "broker": broker, "broker_server": server_name,
        "bid": 2650.1 + shift, "ask": 2650.3 + shift, "spread": 0.2,
        "tick_volume_current": 10, "tick_volume_closed": history[-1]["tick_volume"],
        "last_closed_bar": newest, "broker_time": newest + 300,
        "broker_time_msc": (newest + 300) * 1000,
        "broker_utc_offset_seconds": 7200, "ea_version": "bridge-0.4",
        "m5_bars": history,
    }


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        server.DB = Path(self.temp.name) / "integration.sqlite3"
        server.latest_by_stream.clear()
        server.analysis_by_stream.clear()
        server.clients.clear()
        server.init_db()

    def tearDown(self):
        self.temp.cleanup()

    def test_http_sqlite_websocket_and_broker_partition(self):
        first = asyncio.run(
            server.receive_tick(server.TickPayload.model_validate(sample_payload("Broker A", "A-MT5")))
        )
        second = asyncio.run(
            server.receive_tick(server.TickPayload.model_validate(sample_payload("Broker B", "B-MT5", 10)))
        )
        self.assertEqual(320, first["bars_accepted"])
        self.assertEqual(320, second["bars_accepted"])
        revision = sample_payload("Broker A", "A-MT5")
        original_close = revision["m5_bars"][0]["close"]
        revision["m5_bars"][0]["close"] = original_close + 0.05
        conflicts = server._save_bars(
            server.TickPayload.model_validate(revision), "2026-10-11T01:00:00+00:00"
        )
        self.assertEqual(1, conflicts)
        dashboard = server.dashboard_snapshot("XAUUSD")
        self.assertEqual("XAUUSD", dashboard["latest"]["symbol"])
        self.assertEqual(320, dashboard["market_structure"]["bar_count"])
        self.assertEqual(4, len(dashboard["scenarios"]))
        self.assertEqual("PHASE2_MODELS_A_B_C", dashboard["signal_engine"])
        self.assertIn("main_recommendation", dashboard)
        self.assertEqual("SIGNAL_ONLY", dashboard["mode"])
        self.assertFalse(dashboard["execution_enabled"])

        class FakeSocket:
            def __init__(self):
                self.messages = []
            async def send_text(self, message):
                self.messages.append(json.loads(message))

        xau_socket, btc_socket = FakeSocket(), FakeSocket()
        server.clients[xau_socket] = ("XAUUSD", None, "M5")
        server.clients[btc_socket] = ("BTCUSD", None, "M5")
        asyncio.run(server._broadcast({"type": "phase1", "data": dashboard}, ("Broker B|B-MT5", "XAUUSD", "M5")))
        self.assertEqual(1, len(xau_socket.messages))
        self.assertEqual(0, len(btc_socket.messages))

        with closing(sqlite3.connect(server.DB)) as con, con:
            streams = con.execute(
                "SELECT broker_key,symbol,timeframe,COUNT(*) FROM m5_bars "
                "GROUP BY broker_key,symbol,timeframe ORDER BY broker_key"
            ).fetchall()
            stored_close = con.execute(
                "SELECT close FROM m5_bars WHERE broker_key=? AND symbol=? AND timeframe=? AND bar_time=?",
                ("Broker A|A-MT5", "XAUUSD", "M5", BASE),
            ).fetchone()[0]
        self.assertEqual(2, len(streams))
        self.assertEqual([320, 320], [row[3] for row in streams])
        self.assertEqual(original_close, stored_close)

    def test_btc_legacy_is_preserved_but_never_selected_for_xau(self):
        btc = {
            "source": "MT5", "symbol": "BTCUSD", "timeframe": "M5",
            "bid": 60000, "ask": 60001, "spread": 1,
            "tick_volume_current": 1, "tick_volume_closed": 2,
            "last_closed_bar": BASE, "broker_time": BASE + 300,
            "received_utc": "2026-10-11T00:00:00+00:00",
        }
        with closing(sqlite3.connect(server.DB)) as con, con:
            con.execute(
                "INSERT INTO ticks(received_utc,payload) VALUES (?,?)",
                (btc["received_utc"], json.dumps(btc)),
            )
        server.init_db()
        server._load_latest_ticks()
        self.assertIn((server.UNKNOWN_BROKER, "BTCUSD", "M5"), server.latest_by_stream)
        snapshot = server.dashboard_snapshot("XAUUSD")
        self.assertIsNone(snapshot["latest"])
        self.assertEqual("XAUUSD", snapshot["target"]["symbol"])

    def test_legacy_schema_migration_copies_without_deleting(self):
        old_db = server.DB
        with closing(sqlite3.connect(old_db)) as con, con:
            con.execute("DROP TABLE m5_bars")
            con.execute(
                """
                CREATE TABLE m5_bars(
                    symbol TEXT NOT NULL,timeframe TEXT NOT NULL,bar_time INTEGER NOT NULL,
                    bar_time_utc INTEGER NOT NULL,broker_utc_offset_seconds INTEGER NOT NULL,
                    open REAL NOT NULL,high REAL NOT NULL,low REAL NOT NULL,close REAL NOT NULL,
                    tick_volume INTEGER NOT NULL,real_volume INTEGER NOT NULL,spread INTEGER NOT NULL,
                    received_utc TEXT NOT NULL,PRIMARY KEY(symbol,timeframe,bar_time)
                )
                """
            )
            con.execute(
                "INSERT INTO m5_bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("BTCUSD", "M5", BASE, BASE, 0, 10, 11, 9, 10.5, 5, 0, 1, "2026-10-11T00:00:00+00:00"),
            )
            before_ticks = con.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]
        server.init_db()
        with closing(sqlite3.connect(old_db)) as con, con:
            self.assertIsNotNone(con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='m5_bars_legacy_no_broker'"
            ).fetchone())
            self.assertEqual(1, con.execute("SELECT COUNT(*) FROM m5_bars_legacy_no_broker").fetchone()[0])
            copied = con.execute("SELECT broker_key,symbol FROM m5_bars").fetchone()
            self.assertEqual((server.UNKNOWN_BROKER, "BTCUSD"), copied)
            self.assertEqual(before_ticks, con.execute("SELECT COUNT(*) FROM ticks").fetchone()[0])


class DashboardContractTests(unittest.TestCase):
    def test_dashboard_locks_xau_and_exposes_required_fields(self):
        html = (server.ROOT / "web" / "index.html").read_text(encoding="utf-8")
        for text in (
            "const TARGET_SYMBOL='XAUUSD'", "BUY Scenario", "SELL Scenario", "POI Retest",
            "Breakout / Reversal", "Điều kiện đã đạt", "Điều kiện chưa đạt",
            "Tick volume:", "Win Rate: N/A", "Không có OrderSend", "Main Recommendation",
            "Primary Action", "Risk Status", "Historical Win Rate: N/A",
            'href="#signalRegistry"', 'href="#phase1"', 'id="signalRows"',
            'id="phase1Scenarios"', "renderSignals(s.signals)",
            "renderPhase1Scenarios(s.phase1_scenarios)",
        ):
            self.assertIn(text, html)


if __name__ == "__main__":
    unittest.main()
