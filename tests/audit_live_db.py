"""Read-only acceptance audit for the live SQLite file."""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

DB = Path(__file__).resolve().parents[1] / "bridge" / "nta_data.sqlite3"


def main() -> None:
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        print("quick_check=", con.execute("PRAGMA quick_check").fetchone()[0])
        print("tables=", [row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )])
        rows = con.execute("SELECT payload FROM ticks").fetchall()
        streams = Counter()
        offsets = Counter()
        timestamps_valid = 0
        for (raw,) in rows:
            payload = json.loads(raw)
            streams[(
                payload.get("symbol"), payload.get("timeframe"),
                payload.get("broker"), payload.get("broker_server"),
            )] += 1
            offsets[payload.get("broker_utc_offset_seconds")] += 1
            timestamps_valid += int(
                isinstance(payload.get("broker_time"), int)
                and isinstance(payload.get("last_closed_bar"), int)
            )
        print("ticks=", len(rows))
        print("streams=", dict(streams))
        print("explicit_offsets=", dict(offsets))
        print("market_timestamp_rows=", timestamps_valid)
        print("m5_bars=", con.execute("SELECT COUNT(*) FROM m5_bars").fetchone()[0])
        m5_columns = {row[1] for row in con.execute("PRAGMA table_info(m5_bars)")}
        if "broker_key" in m5_columns:
            m5_streams = con.execute(
                "SELECT broker_key,symbol,timeframe,COUNT(*),MIN(bar_time),MAX(bar_time) "
                "FROM m5_bars GROUP BY broker_key,symbol,timeframe ORDER BY broker_key,symbol"
            ).fetchall()
            print("m5_streams=", m5_streams)
            for broker_key, symbol, timeframe, count, first_time, last_time in m5_streams:
                params = (broker_key, symbol, timeframe)
                invalid_ohlc = con.execute(
                    "SELECT COUNT(*) FROM m5_bars WHERE broker_key=? AND symbol=? AND timeframe=? "
                    "AND (high<low OR high<MAX(open,close) OR low>MIN(open,close))", params,
                ).fetchone()[0]
                utc_mismatch = con.execute(
                    "SELECT COUNT(*) FROM m5_bars WHERE broker_key=? AND symbol=? AND timeframe=? "
                    "AND bar_time_utc != bar_time-broker_utc_offset_seconds", params,
                ).fetchone()[0]
                gap_count = con.execute(
                    "SELECT COUNT(*) FROM (SELECT bar_time-LAG(bar_time) OVER (ORDER BY bar_time) gap "
                    "FROM m5_bars WHERE broker_key=? AND symbol=? AND timeframe=?) "
                    "WHERE gap IS NOT NULL AND (gap<=0 OR gap%300!=0)", params,
                ).fetchone()[0]
                latest_tick = con.execute(
                    "SELECT payload,received_utc FROM ticks WHERE broker_key=? AND symbol=? "
                    "AND timeframe=? ORDER BY id DESC LIMIT 1", params,
                ).fetchone()
                newest_is_closed = None
                fresh_seconds = None
                if latest_tick:
                    latest_payload = json.loads(latest_tick[0])
                    newest_is_closed = (
                        int(last_time) + 300 <= int(latest_payload.get("broker_time", 0)) + 5
                        and int(last_time) == int(latest_payload.get("last_closed_bar", -1))
                    )
                    received = datetime.fromisoformat(latest_tick[1])
                    fresh_seconds = round((datetime.now(timezone.utc) - received).total_seconds(), 1)
                print("m5_validation=", {
                    "stream": (broker_key, symbol, timeframe), "bar_count": count,
                    "within_300_350": 300 <= count <= 350,
                    "invalid_ohlc": invalid_ohlc, "invalid_order_or_grid": gap_count,
                    "utc_mismatch": utc_mismatch, "newest_is_closed": newest_is_closed,
                    "age_seconds_at_audit": fresh_seconds,
                    "first_time": first_time, "last_time": last_time,
                })
    finally:
        con.close()


if __name__ == "__main__":
    main()
