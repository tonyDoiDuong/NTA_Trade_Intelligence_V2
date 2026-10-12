"""NTA local MT5 bridge — Phase 2 signal-only analytics, with no order routing."""
from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
from typing import Literal

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from bridge.analytics import analyze_phase1
from bridge.scenario_engine import compose_phase2_dashboard
from bridge.signal_engine import build_phase2_candidates, is_market_open
from bridge.signal_registry import (
    advance_virtual_lifecycle, create_signal_tables, load_signals,
    record_new_signals, signal_history,
)


ROOT = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get("NTA_DB_PATH", ROOT / "bridge" / "nta_data.sqlite3"))
DEFAULT_SYMBOL = os.environ.get("NTA_DASHBOARD_SYMBOL", "XAUUSD").upper()
MAX_ANALYSIS_BARS = 350
LIVE_AFTER_SECONDS = 10
UNKNOWN_BROKER = "UNKNOWN_LEGACY"

app = FastAPI(title="NTA MT5 Bridge", version="0.5.0")
app.mount("/assets", StaticFiles(directory=ROOT / "web"), name="assets")
clients: dict[WebSocket, tuple[str, str | None, str]] = {}
latest_by_stream: dict[tuple[str, str, str], dict] = {}
analysis_by_stream: dict[tuple[str, str, str], dict] = {}
observations_by_stream: dict[tuple[str, str, str], list[dict]] = {}


class M5Bar(BaseModel):
    time: int = Field(ge=0)
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    tick_volume: int = Field(ge=0)
    real_volume: int = Field(default=0, ge=0)
    spread: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_ohlc(self) -> "M5Bar":
        prices = (self.open, self.high, self.low, self.close)
        if not all(math.isfinite(price) for price in prices):
            raise ValueError("OHLC must be finite")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("invalid OHLC range")
        if self.high < self.low:
            raise ValueError("high must be >= low")
        return self


class TickPayload(BaseModel):
    source: Literal["MT5"] = "MT5"
    symbol: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._#+-]+$")
    timeframe: Literal["M5"] = "M5"
    broker: str | None = Field(default=None, max_length=120)
    broker_server: str | None = Field(default=None, max_length=120)
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    spread: float = Field(ge=0)
    tick_volume_current: int = Field(ge=0)
    tick_volume_closed: int = Field(ge=0)
    last_closed_bar: int = Field(ge=0)
    broker_time: int = Field(ge=0)
    broker_time_msc: int = Field(default=0, ge=0)
    broker_utc_offset_seconds: int | None = Field(default=None, ge=-50400, le=50400)
    ea_version: str = Field(default="bridge-0.1", max_length=40)
    m5_bars: list[M5Bar] = Field(default_factory=list, max_length=350)

    @model_validator(mode="after")
    def validate_snapshot(self) -> "TickPayload":
        if not all(math.isfinite(value) for value in (self.bid, self.ask, self.spread)):
            raise ValueError("prices must be finite")
        if self.ask < self.bid:
            raise ValueError("ask must be >= bid")
        expected_spread = self.ask - self.bid
        if abs(self.spread - expected_spread) > max(1e-8, expected_spread * 0.01):
            raise ValueError("spread does not match ask - bid")
        if self.broker_time_msc and abs(self.broker_time_msc // 1000 - self.broker_time) > 2:
            raise ValueError("broker_time_msc does not match broker_time")
        if self.m5_bars:
            if not self.broker or not self.broker_server:
                raise ValueError("broker and broker_server are required with m5_bars")
            if self.broker_utc_offset_seconds is None:
                raise ValueError("broker_utc_offset_seconds is required with m5_bars")
            if not 300 <= len(self.m5_bars) <= 350:
                raise ValueError("m5_bars must contain 300 to 350 closed bars")
            times = [bar.time for bar in self.m5_bars]
            if times != sorted(times) or len(times) != len(set(times)):
                raise ValueError("m5_bars must be unique and strictly oldest-to-newest")
            if any(value % 300 != 0 for value in times):
                raise ValueError("M5 bar timestamps must align to 300-second boundaries")
            if any((newer - older) % 300 != 0 for older, newer in zip(times, times[1:])):
                raise ValueError("M5 bar gaps must be whole M5 intervals")
            if times[-1] != self.last_closed_bar:
                raise ValueError("last_closed_bar must match newest supplied M5 bar")
            if self.last_closed_bar + 300 > self.broker_time + 5:
                raise ValueError("newest M5 bar is not closed at broker_time")
        return self

    def stream_key(self) -> tuple[str, str, str]:
        broker_key = f"{self.broker}|{self.broker_server}" if self.broker and self.broker_server else UNKNOWN_BROKER
        return broker_key, self.symbol.upper(), self.timeframe


def _connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def _table_columns(con: sqlite3.Connection, name: str) -> set[str]:
    return {row["name"] for row in con.execute(f"PRAGMA table_info({name})")}


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _create_m5_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS m5_bars (
            broker_key TEXT NOT NULL,
            broker TEXT NOT NULL,
            broker_server TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            bar_time INTEGER NOT NULL,
            bar_time_utc INTEGER NOT NULL,
            broker_utc_offset_seconds INTEGER NOT NULL,
            offset_basis TEXT NOT NULL,
            open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
            tick_volume INTEGER NOT NULL, real_volume INTEGER NOT NULL, spread INTEGER NOT NULL,
            received_utc TEXT NOT NULL,
            PRIMARY KEY(broker_key, symbol, timeframe, bar_time)
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_m5_stream_time "
        "ON m5_bars(broker_key,symbol,timeframe,bar_time)"
    )


def _migrate_m5_without_deleting(con: sqlite3.Connection) -> None:
    if _table_exists(con, "m5_bars") and "broker_key" not in _table_columns(con, "m5_bars"):
        if not _table_exists(con, "m5_bars_legacy_no_broker"):
            con.execute("ALTER TABLE m5_bars RENAME TO m5_bars_legacy_no_broker")
        else:
            raise RuntimeError("legacy M5 table already exists; migration requires manual review")
    _create_m5_table(con)
    if _table_exists(con, "m5_bars_legacy_no_broker"):
        con.execute(
            """
            INSERT OR IGNORE INTO m5_bars(
                broker_key,broker,broker_server,symbol,timeframe,bar_time,bar_time_utc,
                broker_utc_offset_seconds,offset_basis,open,high,low,close,tick_volume,
                real_volume,spread,received_utc
            )
            SELECT ?,?,?,symbol,timeframe,bar_time,bar_time_utc,broker_utc_offset_seconds,
                   'LEGACY_STORED_OFFSET',open,high,low,close,tick_volume,real_volume,spread,received_utc
            FROM m5_bars_legacy_no_broker
            """,
            (UNKNOWN_BROKER, UNKNOWN_BROKER, UNKNOWN_BROKER),
        )


def _backfill_tick_dimensions(con: sqlite3.Connection) -> None:
    rows = con.execute(
        "SELECT id,payload FROM ticks WHERE broker_key IS NULL OR symbol IS NULL OR timeframe IS NULL"
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            continue
        broker = payload.get("broker")
        server = payload.get("broker_server")
        broker_key = f"{broker}|{server}" if broker and server else UNKNOWN_BROKER
        con.execute(
            """
            UPDATE ticks SET broker_key=?,broker=?,broker_server=?,symbol=?,timeframe=?,broker_time_msc=?
            WHERE id=?
            """,
            (
                broker_key,
                broker or UNKNOWN_BROKER,
                server or UNKNOWN_BROKER,
                str(payload.get("symbol", "")).upper() or None,
                payload.get("timeframe", "M5"),
                int(payload.get("broker_time_msc") or int(payload.get("broker_time", 0)) * 1000),
                row["id"],
            ),
        )


def init_db() -> None:
    with closing(_connect()) as con, con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS ticks "
            "(id INTEGER PRIMARY KEY, received_utc TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        existing = _table_columns(con, "ticks")
        for column, data_type in (
            ("broker_key", "TEXT"), ("broker", "TEXT"), ("broker_server", "TEXT"),
            ("symbol", "TEXT"), ("timeframe", "TEXT"), ("broker_time_msc", "INTEGER"),
        ):
            if column not in existing:
                con.execute(f"ALTER TABLE ticks ADD COLUMN {column} {data_type}")
        con.execute("CREATE INDEX IF NOT EXISTS idx_ticks_ts ON ticks(received_utc)")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_ticks_stream_ts "
            "ON ticks(broker_key,symbol,timeframe,received_utc)"
        )
        _backfill_tick_dimensions(con)
        _migrate_m5_without_deleting(con)
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS m5_bar_conflicts (
                id INTEGER PRIMARY KEY, broker_key TEXT NOT NULL, symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL, bar_time INTEGER NOT NULL,
                existing_payload TEXT NOT NULL, incoming_payload TEXT NOT NULL,
                received_utc TEXT NOT NULL
            )
            """
        )
        create_signal_tables(con)


def _record_key(record: dict) -> tuple[str, str, str]:
    broker = record.get("broker")
    server = record.get("broker_server")
    broker_key = record.get("broker_key") or (
        f"{broker}|{server}" if broker and server else UNKNOWN_BROKER
    )
    return broker_key, str(record.get("symbol", "")).upper(), record.get("timeframe", "M5")


def _load_latest_ticks() -> None:
    latest_by_stream.clear()
    with closing(_connect()) as con, con:
        rows = con.execute(
            """
            SELECT payload,broker_key FROM (
                SELECT payload,broker_key,
                       ROW_NUMBER() OVER(
                           PARTITION BY broker_key,symbol,timeframe ORDER BY id DESC
                       ) AS stream_rank
                FROM ticks WHERE symbol IS NOT NULL
            ) WHERE stream_rank=1
            """
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            continue
        payload["broker_key"] = row["broker_key"] or _record_key(payload)[0]
        key = _record_key(payload)
        if key[1] and key not in latest_by_stream:
            latest_by_stream[key] = payload


def _load_bars(key: tuple[str, str, str]) -> list[dict]:
    with closing(_connect()) as con, con:
        rows = con.execute(
            """
            SELECT bar_time AS time,open,high,low,close,tick_volume,real_volume,spread
            FROM m5_bars
            WHERE broker_key=? AND symbol=? AND timeframe=?
            ORDER BY bar_time DESC LIMIT ?
            """,
            (*key, MAX_ANALYSIS_BARS),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def _bar_values(bar: M5Bar) -> tuple:
    return (bar.open, bar.high, bar.low, bar.close, bar.tick_volume, bar.real_volume, bar.spread)


def _save_bars(payload: TickPayload, received_utc: str) -> int:
    if not payload.m5_bars:
        return 0
    key = payload.stream_key()
    offset = int(payload.broker_utc_offset_seconds or 0)
    conflicts = 0
    with closing(_connect()) as con, con:
        for bar in payload.m5_bars:
            existing = con.execute(
                """
                SELECT open,high,low,close,tick_volume,real_volume,spread FROM m5_bars
                WHERE broker_key=? AND symbol=? AND timeframe=? AND bar_time=?
                """,
                (*key, bar.time),
            ).fetchone()
            if existing is not None:
                stored = tuple(existing)
                incoming = _bar_values(bar)
                if stored != incoming:
                    conflicts += 1
                    con.execute(
                        """
                        INSERT INTO m5_bar_conflicts(
                            broker_key,symbol,timeframe,bar_time,existing_payload,incoming_payload,received_utc
                        ) VALUES (?,?,?,?,?,?,?)
                        """,
                        (*key, bar.time, json.dumps(stored), json.dumps(incoming), received_utc),
                    )
                continue
            con.execute(
                """
                INSERT INTO m5_bars(
                    broker_key,broker,broker_server,symbol,timeframe,bar_time,bar_time_utc,
                    broker_utc_offset_seconds,offset_basis,open,high,low,close,tick_volume,
                    real_volume,spread,received_utc
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    key[0], payload.broker, payload.broker_server, key[1], key[2], bar.time,
                    bar.time - offset, offset, "CURRENT_OFFSET_AT_RECEIPT",
                    *_bar_values(bar), received_utc,
                ),
            )
    return conflicts


def _iso_from_broker_epoch(seconds: int, offset_seconds: int) -> str:
    return datetime.fromtimestamp(seconds - offset_seconds, timezone.utc).isoformat()


def _decorate_tick(record: dict) -> dict:
    decorated = dict(record)
    decorated["broker_key"] = _record_key(decorated)[0]
    offset_value = decorated.get("broker_utc_offset_seconds")
    if offset_value is None:
        decorated["broker_time_utc"] = None
        decorated["last_closed_bar_utc"] = None
        decorated["timezone_status"] = "UNKNOWN_LEGACY_PAYLOAD"
    else:
        offset = int(offset_value)
        decorated["broker_time_utc"] = _iso_from_broker_epoch(int(decorated["broker_time"]), offset)
        decorated["last_closed_bar_utc"] = _iso_from_broker_epoch(
            int(decorated["last_closed_bar"]), offset
        )
        decorated["timezone_status"] = "CURRENT_OFFSET_AT_RECEIPT"
    return decorated


def _is_live(record: dict | None) -> bool:
    if not record:
        return False
    try:
        received = datetime.fromisoformat(record["received_utc"])
    except (KeyError, TypeError, ValueError):
        return False
    return datetime.now(timezone.utc) - received <= timedelta(seconds=LIVE_AFTER_SECONDS)


def _latest_record(
    symbol: str = DEFAULT_SYMBOL, broker_key: str | None = None, timeframe: str = "M5"
) -> dict | None:
    candidates = [
        record for key, record in latest_by_stream.items()
        if key[1] == symbol.upper() and key[2] == timeframe and (broker_key is None or key[0] == broker_key)
    ]
    return max(candidates, key=lambda item: item.get("received_utc", "")) if candidates else None


def _analysis_for(record: dict | None) -> dict:
    if not record:
        return analyze_phase1([])
    key = _record_key(record)
    if key not in analysis_by_stream:
        analysis_by_stream[key] = analyze_phase1(
            _load_bars(key), int(record.get("broker_utc_offset_seconds") or 0)
        )
    return analysis_by_stream[key]


def dashboard_snapshot(
    symbol: str = DEFAULT_SYMBOL, broker_key: str | None = None, timeframe: str = "M5"
) -> dict:
    record = _latest_record(symbol, broker_key, timeframe)
    analysis = _analysis_for(record)
    key = _record_key(record) if record else (
        broker_key or UNKNOWN_BROKER, symbol.upper(), timeframe
    )
    persisted_signals = load_signals(_connect, key) if record else []
    observations = observations_by_stream.get(key)
    if observations is None and record:
        evaluated = build_phase2_candidates(
            _load_bars(key), analysis, {**record, "data_fresh": _is_live(record)},
            key, persisted_signals,
        )
        observations = evaluated["observations"]
    phase2 = compose_phase2_dashboard(
        persisted_signals, observations or [],
        connected=_is_live(record),
        market_open=is_market_open(record) if record else False,
        current_spread=float(record["spread"]) if record else None,
    )
    latest_bar_time = int(record.get("last_closed_bar", 0)) if record else 0
    recommendation = phase2["main_recommendation"]
    confirmation = recommendation.get("signal_id") and next(
        (
            int(signal.get("confirmation_timestamp") or 0)
            for signal in phase2["signals"]
            if signal.get("signal_id") == recommendation.get("signal_id")
        ),
        0,
    )
    if confirmation and latest_bar_time >= confirmation:
        recommendation["signal_age_bars"] = (latest_bar_time - confirmation) // 300
    return {
        "connected": _is_live(record),
        "target": {"symbol": symbol.upper(), "broker_key": broker_key, "timeframe": timeframe},
        "latest": _decorate_tick(record) if record else None,
        **analysis,
        "phase1_scenarios": analysis.get("scenarios", []),
        **phase2,
        "historical_winrate": None,
        "sample_size": None,
        "expectancy": None,
        "mode": "SIGNAL_ONLY",
        "signal_engine": "PHASE2_MODELS_A_B_C",
        "execution_enabled": False,
    }


async def _broadcast(message: dict, key: tuple[str, str, str]) -> None:
    sockets = [
        socket for socket, subscription in clients.items()
        if subscription[0] == key[1] and subscription[2] == key[2]
        and (subscription[1] is None or subscription[1] == key[0])
    ]
    if not sockets:
        return
    text = json.dumps(message, ensure_ascii=False)
    results = await asyncio.gather(
        *(socket.send_text(text) for socket in sockets), return_exceptions=True
    )
    for socket, result in zip(sockets, results):
        if isinstance(result, Exception):
            clients.pop(socket, None)


init_db()
_load_latest_ticks()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/api/health")
def health() -> dict:
    record = _latest_record()
    return {
        "status": "ok",
        "bridge_live": _is_live(record),
        "target_symbol": DEFAULT_SYMBOL,
        "latest_received_utc": record.get("received_utc") if record else None,
        "phase": 2,
        "version": app.version,
    }


@app.get("/api/dashboard")
def dashboard(
    symbol: str = Query(DEFAULT_SYMBOL, min_length=1, max_length=40),
    broker_key: str | None = Query(default=None, max_length=250),
    timeframe: Literal["M5"] = "M5",
) -> dict:
    return dashboard_snapshot(symbol, broker_key, timeframe)


@app.post("/api/mt5/tick")
async def receive_tick(payload: TickPayload) -> dict:
    key = payload.stream_key()
    incoming_msc = payload.broker_time_msc or payload.broker_time * 1000
    previous = latest_by_stream.get(key)
    if previous:
        previous_msc = previous.get("broker_time_msc") or previous.get("broker_time", 0) * 1000
        if incoming_msc <= previous_msc:
            return {"ok": True, "ignored": "duplicate_or_out_of_order_tick"}

    received_utc = datetime.now(timezone.utc).isoformat()
    record = payload.model_dump(exclude={"m5_bars"})
    record.update({
        "symbol": payload.symbol.upper(),
        "broker_key": key[0],
        "spread": payload.ask - payload.bid,
        "received_utc": received_utc,
    })
    conflicts = _save_bars(payload, received_utc)
    with closing(_connect()) as con, con:
        con.execute(
            """
            INSERT INTO ticks(
                received_utc,payload,broker_key,broker,broker_server,symbol,timeframe,broker_time_msc
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                received_utc, json.dumps(record, ensure_ascii=False), key[0],
                payload.broker or UNKNOWN_BROKER, payload.broker_server or UNKNOWN_BROKER,
                key[1], key[2], incoming_msc,
            ),
        )

    latest_by_stream[key] = record
    analysis_by_stream[key] = analyze_phase1(
        _load_bars(key), int(payload.broker_utc_offset_seconds or 0)
    )
    advance_virtual_lifecycle(
        _connect, key,
        {**record, "data_fresh": True, "market_open": is_market_open(record)},
        received_utc,
    )
    registry_context = load_signals(
        _connect, key, {"DETECTED", "CONFIRMED", "READY", "ARMED", "VIRTUAL_FILLED"}
    )
    phase2_evaluation = build_phase2_candidates(
        _load_bars(key), analysis_by_stream[key],
        {**record, "data_fresh": True}, key, registry_context,
    )
    observations_by_stream[key] = phase2_evaluation["observations"]
    inserted_signals = record_new_signals(
        _connect, phase2_evaluation["new_signals"], received_utc
    )
    snapshot = dashboard_snapshot(key[1], key[0], key[2])
    await _broadcast({"type": "tick", "data": snapshot["latest"]}, key)
    await _broadcast({"type": "phase1", "data": snapshot}, key)
    return {
        "ok": True,
        "received_utc": received_utc,
        "bars_accepted": len(payload.m5_bars),
        "immutable_bar_conflicts": conflicts,
        "signals_created": len(inserted_signals),
        "stream": {"broker_key": key[0], "symbol": key[1], "timeframe": key[2]},
    }


@app.get("/api/signals/{signal_id}")
def get_signal_history(signal_id: str) -> dict:
    history = signal_history(_connect, signal_id)
    if history is None:
        return {"found": False, "signal_id": signal_id}
    return {"found": True, **history}


@app.websocket("/ws/live")
async def live(ws: WebSocket) -> None:
    symbol = ws.query_params.get("symbol", DEFAULT_SYMBOL).upper()
    broker_key = ws.query_params.get("broker_key")
    timeframe = ws.query_params.get("timeframe", "M5")
    if timeframe != "M5":
        await ws.close(code=1008)
        return
    await ws.accept()
    clients[ws] = (symbol, broker_key, timeframe)
    try:
        await ws.send_json({
            "type": "phase1",
            "data": dashboard_snapshot(symbol, broker_key, timeframe),
        })
        while True:
            await asyncio.sleep(5)
            await ws.send_json({
                "type": "heartbeat",
                "server_utc": datetime.now(timezone.utc).isoformat(),
            })
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        clients.pop(ws, None)
