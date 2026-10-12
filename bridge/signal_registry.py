"""Append-only signal snapshots and virtual lifecycle persistence in SQLite."""
from __future__ import annotations

from contextlib import closing
import json
import sqlite3
from typing import Any, Callable

from bridge.risk_engine import DEFAULT_RISK_CONFIG, calculate_rr
from bridge.signal_schema import assert_transition, validate_ready_contract

ConnectionFactory = Callable[[], sqlite3.Connection]


def create_signal_tables(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (
            signal_id TEXT PRIMARY KEY,
            trade_idea_id TEXT NOT NULL,
            broker_key TEXT NOT NULL, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
            method TEXT NOT NULL, direction TEXT NOT NULL,
            confirmation_timestamp INTEGER NOT NULL,
            current_status TEXT NOT NULL, current_payload TEXT NOT NULL,
            created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL,
            UNIQUE(broker_key,symbol,timeframe,method,trade_idea_id,confirmation_timestamp)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS signal_transitions (
            id INTEGER PRIMARY KEY,
            signal_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            from_status TEXT,
            to_status TEXT NOT NULL,
            transition_utc TEXT NOT NULL,
            reason TEXT NOT NULL,
            UNIQUE(signal_id,sequence)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS signal_snapshots (
            id INTEGER PRIMARY KEY,
            signal_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_utc TEXT NOT NULL,
            UNIQUE(signal_id,sequence)
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_signals_stream_status "
        "ON signals(broker_key,symbol,timeframe,current_status,confirmation_timestamp)"
    )


def _loads(raw: str) -> dict[str, Any]:
    return json.loads(raw)


def load_signals(
    connect: ConnectionFactory,
    stream_key: tuple[str, str, str],
    statuses: set[str] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    query = (
        "SELECT current_payload FROM signals "
        "WHERE broker_key=? AND symbol=? AND timeframe=?"
    )
    params: list[Any] = list(stream_key)
    if statuses:
        placeholders = ",".join("?" for _ in statuses)
        query += f" AND current_status IN ({placeholders})"
        params.extend(sorted(statuses))
    query += " ORDER BY confirmation_timestamp DESC, updated_utc DESC LIMIT ?"
    params.append(limit)
    with closing(connect()) as con:
        rows = con.execute(query, params).fetchall()
    return [_loads(row["current_payload"]) for row in rows]


def _snapshot_for_status(signal: dict[str, Any], status: str) -> dict[str, Any]:
    snapshot = dict(signal)
    snapshot["status"] = status
    if status not in {"READY", "ARMED", "VIRTUAL_FILLED", "CLOSED_TP", "CLOSED_SL"}:
        snapshot["action"] = "WAIT"
    return snapshot


def record_new_signals(
    connect: ConnectionFactory,
    signals: list[dict[str, Any]],
    transition_utc: str,
) -> list[dict[str, Any]]:
    inserted: list[dict[str, Any]] = []
    with closing(connect()) as con, con:
        for signal in signals:
            stream = signal["stream"]
            existing = con.execute(
                """
                SELECT signal_id FROM signals
                WHERE broker_key=? AND symbol=? AND timeframe=? AND method=?
                  AND trade_idea_id=? AND confirmation_timestamp=?
                """,
                (
                    stream["broker_key"], stream["symbol"], stream["timeframe"],
                    signal["method"], signal["trade_idea_id"], signal["confirmation_timestamp"],
                ),
            ).fetchone()
            if existing:
                continue
            validate_ready_contract(signal)
            con.execute(
                """
                INSERT INTO signals(
                    signal_id,trade_idea_id,broker_key,symbol,timeframe,method,direction,
                    confirmation_timestamp,current_status,current_payload,created_utc,updated_utc
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    signal["signal_id"], signal["trade_idea_id"], stream["broker_key"],
                    stream["symbol"], stream["timeframe"], signal["method"], signal["direction"],
                    signal["confirmation_timestamp"], signal["status"],
                    json.dumps(signal, ensure_ascii=False), signal.get("created_utc") or transition_utc,
                    transition_utc,
                ),
            )
            previous = None
            for sequence, status in enumerate(signal["state_path"]):
                if previous is not None:
                    assert_transition(previous, status)
                snapshot = _snapshot_for_status(signal, status)
                con.execute(
                    """
                    INSERT INTO signal_transitions(
                        signal_id,sequence,from_status,to_status,transition_utc,reason
                    ) VALUES (?,?,?,?,?,?)
                    """,
                    (
                        signal["signal_id"], sequence, previous, status, transition_utc,
                        "INITIAL_EVALUATION",
                    ),
                )
                con.execute(
                    """
                    INSERT INTO signal_snapshots(signal_id,sequence,status,payload,created_utc)
                    VALUES (?,?,?,?,?)
                    """,
                    (
                        signal["signal_id"], sequence, status,
                        json.dumps(snapshot, ensure_ascii=False), transition_utc,
                    ),
                )
                previous = status
            inserted.append(signal)
    return inserted


def _next_sequence(con: sqlite3.Connection, signal_id: str) -> int:
    return int(con.execute(
        "SELECT COALESCE(MAX(sequence),-1)+1 FROM signal_snapshots WHERE signal_id=?",
        (signal_id,),
    ).fetchone()[0])


def transition_signal(
    con: sqlite3.Connection,
    signal: dict[str, Any],
    new_status: str,
    transition_utc: str,
    reason: str,
) -> dict[str, Any]:
    previous = signal["status"]
    assert_transition(previous, new_status)
    updated = dict(signal)
    updated["status"] = new_status
    updated["updated_utc"] = transition_utc
    updated["state_path"] = list(signal.get("state_path") or []) + [new_status]
    if new_status in {"EXPIRED", "INVALIDATED", "BLOCKED", "CLOSED_TP", "CLOSED_SL"}:
        updated["action"] = "WAIT"
    validate_ready_contract(updated)
    sequence = _next_sequence(con, signal["signal_id"])
    con.execute(
        """
        INSERT INTO signal_transitions(
            signal_id,sequence,from_status,to_status,transition_utc,reason
        ) VALUES (?,?,?,?,?,?)
        """,
        (signal["signal_id"], sequence, previous, new_status, transition_utc, reason),
    )
    con.execute(
        "INSERT INTO signal_snapshots(signal_id,sequence,status,payload,created_utc) VALUES (?,?,?,?,?)",
        (
            signal["signal_id"], sequence, new_status,
            json.dumps(updated, ensure_ascii=False), transition_utc,
        ),
    )
    con.execute(
        "UPDATE signals SET current_status=?,current_payload=?,updated_utc=? WHERE signal_id=?",
        (
            new_status, json.dumps(updated, ensure_ascii=False), transition_utc,
            signal["signal_id"],
        ),
    )
    return updated


def advance_virtual_lifecycle(
    connect: ConnectionFactory,
    stream_key: tuple[str, str, str],
    quote: dict[str, Any],
    transition_utc: str,
) -> list[dict[str, Any]]:
    active = load_signals(connect, stream_key, {"ARMED", "VIRTUAL_FILLED"})
    changed: list[dict[str, Any]] = []
    bid, ask = float(quote["bid"]), float(quote["ask"])
    spread = float(quote.get("spread") or ask - bid)
    tick_msc = int(quote.get("broker_time_msc") or int(quote["broker_time"]) * 1000)
    broker_time = int(quote["broker_time"])
    with closing(connect()) as con, con:
        for signal in active:
            plan = dict(signal["entry_plan"])
            direction = signal["direction"]
            atr = signal.get("atr14")
            spread_high = (
                atr is not None and float(atr) > 0
                and spread / float(atr) > DEFAULT_RISK_CONFIG.max_spread_atr_ratio
            )
            allow_price_actions = (
                bool(quote.get("data_fresh", True))
                and bool(quote.get("market_open", True))
                and not spread_high
            )
            if broker_time > int(plan["expiry_timestamp"]):
                changed.append(transition_signal(
                    con, signal, "EXPIRED", transition_utc, "EXPIRY_TIMESTAMP_REACHED"
                ))
                continue
            invalidated = (
                bid <= float(plan["invalidation_price"]) if direction == "BUY"
                else ask >= float(plan["invalidation_price"])
            )
            if signal["status"] == "ARMED" and allow_price_actions and invalidated:
                changed.append(transition_signal(
                    con, signal, "INVALIDATED", transition_utc, "PRICE_CROSSED_INVALIDATION"
                ))
                continue
            if (
                signal["status"] == "ARMED" and allow_price_actions
                and tick_msc > int(signal["created_tick_msc"])
            ):
                entry_type = plan["entry_type"]
                market_fill = entry_type.endswith("_MARKET")
                limit_fill = (
                    entry_type == "BUY_LIMIT" and ask <= float(plan["entry_price"])
                ) or (
                    entry_type == "SELL_LIMIT" and bid >= float(plan["entry_price"])
                )
                if market_fill or limit_fill:
                    fill_price = ask if direction == "BUY" else bid
                    if limit_fill:
                        fill_price = float(plan["entry_price"])
                    rr = calculate_rr(
                        direction, fill_price, float(plan["stop_loss"]),
                        float(plan["take_profit"]), spread,
                    )
                    if rr["rr_after_costs"] is None or float(rr["rr_after_costs"]) < 2.0:
                        changed.append(transition_signal(
                            con, signal, "BLOCKED", transition_utc, "RR_DROPPED_BELOW_2_AT_FILL"
                        ))
                        continue
                    filled = dict(signal)
                    filled["entry_plan"] = {
                        **plan, **rr, "entry_price": round(fill_price, 6),
                        "virtual_fill_tick_msc": tick_msc,
                        "virtual_fill_utc": transition_utc,
                    }
                    changed.append(transition_signal(
                        con, filled, "VIRTUAL_FILLED", transition_utc, "FORWARD_TICK_VIRTUAL_FILL"
                    ))
                    continue
            if signal["status"] == "VIRTUAL_FILLED" and allow_price_actions:
                stop_hit = (
                    bid <= float(plan["stop_loss"]) if direction == "BUY"
                    else ask >= float(plan["stop_loss"])
                )
                tp_hit = (
                    bid >= float(plan["take_profit"]) if direction == "BUY"
                    else ask <= float(plan["take_profit"])
                )
                if stop_hit:
                    changed.append(transition_signal(
                        con, signal, "CLOSED_SL", transition_utc, "FORWARD_TICK_STOP_HIT"
                    ))
                elif tp_hit:
                    changed.append(transition_signal(
                        con, signal, "CLOSED_TP", transition_utc, "FORWARD_TICK_TP_HIT"
                    ))
    return changed


def signal_history(connect: ConnectionFactory, signal_id: str) -> dict[str, Any] | None:
    with closing(connect()) as con:
        current = con.execute(
            "SELECT current_payload FROM signals WHERE signal_id=?", (signal_id,)
        ).fetchone()
        if not current:
            return None
        transitions = [
            dict(row) for row in con.execute(
                "SELECT sequence,from_status,to_status,transition_utc,reason "
                "FROM signal_transitions WHERE signal_id=? ORDER BY sequence",
                (signal_id,),
            ).fetchall()
        ]
        snapshots = [
            {"sequence": row["sequence"], "status": row["status"], "payload": _loads(row["payload"])}
            for row in con.execute(
                "SELECT sequence,status,payload FROM signal_snapshots "
                "WHERE signal_id=? ORDER BY sequence",
                (signal_id,),
            ).fetchall()
        ]
    return {"current": _loads(current["current_payload"]), "transitions": transitions, "snapshots": snapshots}
