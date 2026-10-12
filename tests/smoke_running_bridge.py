"""End-to-end HTTP/WebSocket smoke against a disposable Bridge database."""
from __future__ import annotations

import asyncio
import json
from urllib.request import Request, urlopen

import websockets

BASE_URL = "http://127.0.0.1:8014"
BASE = 1_700_000_100


def payload() -> dict:
    bars = []
    for index in range(320):
        price = 2600 + (index % 9) * 0.1
        bars.append({
            "time": BASE + index * 300,
            "open": price, "high": price + 0.5, "low": price - 0.5,
            "close": price + 0.1, "tick_volume": 100 + index % 20,
            "real_volume": 0, "spread": 20,
        })
    newest = bars[-1]["time"]
    return {
        "source": "MT5", "symbol": "XAUUSD", "timeframe": "M5",
        "broker": "SYNTHETIC_TEST_ONLY", "broker_server": "DISPOSABLE",
        "bid": 2650.1, "ask": 2650.3, "spread": 0.2,
        "tick_volume_current": 10, "tick_volume_closed": bars[-1]["tick_volume"],
        "last_closed_bar": newest, "broker_time": newest + 300,
        "broker_time_msc": (newest + 300) * 1000,
        "broker_utc_offset_seconds": 7200, "ea_version": "test-only",
        "m5_bars": bars,
    }


def request_json(url: str, data: dict | None = None) -> dict:
    body = json.dumps(data).encode() if data is not None else None
    request = Request(url, data=body, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=10) as response:
        return json.load(response)


async def websocket_check() -> dict:
    async with websockets.connect("ws://127.0.0.1:8014/ws/live?symbol=XAUUSD&timeframe=M5") as ws:
        return json.loads(await asyncio.wait_for(ws.recv(), timeout=10))


def main() -> None:
    health = request_json(f"{BASE_URL}/api/health")
    posted = request_json(f"{BASE_URL}/api/mt5/tick", payload())
    dashboard = request_json(f"{BASE_URL}/api/dashboard?symbol=XAUUSD&timeframe=M5")
    message = asyncio.run(websocket_check())
    assert health["phase"] == 2
    assert health["version"] == "0.5.0"
    assert posted["bars_accepted"] == 320
    assert dashboard["latest"]["symbol"] == "XAUUSD"
    assert dashboard["market_structure"]["bar_count"] == 320
    assert len(dashboard["scenarios"]) == 4
    assert dashboard["signal_engine"] == "PHASE2_MODELS_A_B_C"
    assert dashboard["mode"] == "SIGNAL_ONLY"
    assert dashboard["execution_enabled"] is False
    assert "main_recommendation" in dashboard
    assert message["type"] == "phase1"
    assert message["data"]["signal_engine"] == "PHASE2_MODELS_A_B_C"
    print("SMOKE PASS: HTTP POST -> SQLite -> Phase 1/2 engines -> HTTP GET -> WebSocket")


if __name__ == "__main__":
    main()
