"""SQLite persistence so SMS customers survive restarts: linked sessions (settings, opt-in,
what we already texted), the storm clock, and the message transcript.

The audit log deliberately never holds message text; the transcript does, so it lives in
its own table. Delete a customer's thread with `delete_thread(phone)`.
"""
from __future__ import annotations

import base64
import json
import os
import pickle
import sqlite3
from datetime import datetime

from .config import LOG_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (phone TEXT PRIMARY KEY, state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS world (id INTEGER PRIMARY KEY CHECK (id = 1), state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT NOT NULL, ts TEXT NOT NULL,
    direction TEXT NOT NULL, text TEXT NOT NULL, kind TEXT, status TEXT);
CREATE INDEX IF NOT EXISTS messages_phone ON messages (phone, id);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ops_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, storm_h REAL NOT NULL, kind TEXT NOT NULL,
    circuit_id TEXT, payload TEXT NOT NULL, author TEXT, undone INTEGER NOT NULL DEFAULT 0);
"""


def _db_path() -> str:
    return os.environ.get("CONCIERGE_DB") or str(LOG_DIR / "concierge.db")


def _conn() -> sqlite3.Connection:
    path = _db_path()
    if path != ":memory:":
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    c = sqlite3.connect(path, timeout=10)
    c.executescript(SCHEMA)
    return c


# ---------- sessions ----------

def _encode(s) -> str:
    return json.dumps({
        "customer": s.customer, "battery_mode": s.battery_mode, "emoji": s.emoji,
        "last_sent": s.last_sent, "outage_announced": s.outage_announced,
        "restored_announced": s.restored_announced,
        "escalations_sent": [list(k) for k in s.escalations_sent],
        # Scenario holds engine dataclasses, so it travels as a pickle of our own writing.
        "last_scenario": base64.b64encode(pickle.dumps(s.last_scenario)).decode() if s.last_scenario else None,
    }, default=str)


def _decode(cls, raw: str):
    d = json.loads(raw)
    d["escalations_sent"] = {tuple(k) for k in d["escalations_sent"]}
    sc = d.pop("last_scenario")
    s = cls(**d)
    s.last_scenario = pickle.loads(base64.b64decode(sc)) if sc else None
    return s


def save_session(phone: str, session) -> None:
    with _conn() as c:
        c.execute("INSERT INTO sessions (phone, state) VALUES (?, ?) "
                  "ON CONFLICT(phone) DO UPDATE SET state = excluded.state", (phone, _encode(session)))


def load_sessions(cls) -> dict:
    with _conn() as c:
        return {p: _decode(cls, st) for p, st in c.execute("SELECT phone, state FROM sessions")}


# ---------- world ----------

def save_world(world) -> None:
    state = json.dumps({"phase": world.phase, "t_h": world.t_h, "what_if": world.what_if})
    with _conn() as c:
        c.execute("INSERT INTO world (id, state) VALUES (1, ?) "
                  "ON CONFLICT(id) DO UPDATE SET state = excluded.state", (state,))


def load_world() -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT state FROM world WHERE id = 1").fetchone()
    return json.loads(row[0]) if row else None


# ---------- transcript ----------

def log_message(phone: str, direction: str, text: str, kind: str | None = None, status: str | None = None) -> None:
    with _conn() as c:
        c.execute("INSERT INTO messages (phone, ts, direction, text, kind, status) VALUES (?, ?, ?, ?, ?, ?)",
                  (phone, datetime.now().isoformat(timespec="seconds"), direction, text, kind, status))


def thread(phone: str, limit: int = 200) -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT id, ts, direction, text, kind, status FROM messages "
                         "WHERE phone = ? ORDER BY id DESC LIMIT ?", (phone, limit)).fetchall()
    return [dict(zip(("id", "ts", "dir", "text", "kind", "status"), r)) for r in reversed(rows)]


def delete_thread(phone: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM messages WHERE phone = ?", (phone,))


def clear() -> None:
    with _conn() as c:
        c.executescript("DELETE FROM sessions; DELETE FROM world; DELETE FROM messages;")


# ---------- operations actions (staff fixes) ----------

_OPS_COLS = ("id", "ts", "storm_h", "kind", "circuit_id", "payload", "author", "undone")


def _ops_row(r) -> dict:
    d = dict(zip(_OPS_COLS, r))
    d["payload"], d["undone"] = json.loads(d["payload"]), bool(d["undone"])
    return d


def add_ops_action(kind: str, circuit_id: str | None, payload: dict, storm_h: float, author: str = "ops") -> dict:
    with _conn() as c:
        cur = c.execute("INSERT INTO ops_actions (ts, storm_h, kind, circuit_id, payload, author) VALUES (?, ?, ?, ?, ?, ?)",
                        (datetime.now().isoformat(timespec="seconds"), storm_h, kind, circuit_id, json.dumps(payload), author))
        row = c.execute(f"SELECT {', '.join(_OPS_COLS)} FROM ops_actions WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _ops_row(row)


def ops_actions() -> list[dict]:
    with _conn() as c:
        return [_ops_row(r) for r in c.execute(f"SELECT {', '.join(_OPS_COLS)} FROM ops_actions ORDER BY id")]


def undo_ops_action(action_id: int) -> None:
    with _conn() as c:
        c.execute("UPDATE ops_actions SET undone = 1 WHERE id = ?", (action_id,))


def clear_ops() -> None:
    with _conn() as c:
        c.execute("DELETE FROM ops_actions")
        c.execute("INSERT INTO meta (k, v) VALUES ('reset_at', ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                  (datetime.now().isoformat(timespec="seconds"),))


def reset_at() -> str:
    """When the demo was last reset (staff queue items older than this are hidden)."""
    with _conn() as c:
        row = c.execute("SELECT v FROM meta WHERE k = 'reset_at'").fetchone()
    return row[0] if row else ""
