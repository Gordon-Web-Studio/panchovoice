"""Small sqlite index of suites and runs (data_dir/panchovoice.db). The run files stay the source of truth."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS suites (
    suite_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    agent_id TEXT,
    total INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    passed INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    pass_rate REAL,
    gate_passed INTEGER,
    summary_json TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    suite_id TEXT,
    scenario_id TEXT NOT NULL,
    agent_id TEXT,
    risk TEXT,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    duration_ms INTEGER,
    latency_p50_ms INTEGER,
    latency_p95_ms INTEGER,
    wer REAL,
    path TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_suite ON runs(suite_id);
CREATE INDEX IF NOT EXISTS runs_scenario ON runs(scenario_id, started_at);
CREATE INDEX IF NOT EXISTS runs_started ON runs(started_at);
"""

_lock = threading.Lock()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path), timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


@contextmanager
def _open(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Connect, commit on success, always close (Windows keeps open files locked)."""
    con = connect(db_path)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    if "gate_passed" in d and d["gate_passed"] is not None:
        d["gate_passed"] = bool(d["gate_passed"])
    return d


def upsert_suite(db_path: Path, summary: dict[str, Any]) -> None:
    with _lock, _open(db_path) as con:
        con.execute(
            """INSERT INTO suites (suite_id, started_at, finished_at, status, agent_id, total, done, passed, failed,
                                   errors, pass_rate, gate_passed, summary_json)
               VALUES (:suite_id, :started_at, :finished_at, :status, :agent_id, :total, :done, :passed, :failed,
                       :errors, :pass_rate, :gate_passed, :summary_json)
               ON CONFLICT(suite_id) DO UPDATE SET finished_at=excluded.finished_at, status=excluded.status,
                   total=excluded.total, done=excluded.done, passed=excluded.passed, failed=excluded.failed,
                   errors=excluded.errors, pass_rate=excluded.pass_rate, gate_passed=excluded.gate_passed,
                   summary_json=excluded.summary_json""",
            {
                "suite_id": summary["suite_id"],
                "started_at": summary["started_at"],
                "finished_at": summary.get("finished_at"),
                "status": summary.get("status", "running"),
                "agent_id": summary.get("agent_id"),
                "total": summary.get("total", 0),
                "done": summary.get("done", 0),
                "passed": summary.get("passed", 0),
                "failed": summary.get("failed", 0),
                "errors": summary.get("errors", 0),
                "pass_rate": summary.get("pass_rate"),
                "gate_passed": None if summary.get("gate_passed") is None else int(bool(summary["gate_passed"])),
                "summary_json": json.dumps(summary),
            },
        )


def insert_run(db_path: Path, result: dict[str, Any], path: Path, risk: str | None = None) -> None:
    m = result.get("metrics") or {}
    with _lock, _open(db_path) as con:
        con.execute(
            """INSERT OR REPLACE INTO runs (run_id, suite_id, scenario_id, agent_id, risk, status, started_at,
                   duration_ms, latency_p50_ms, latency_p95_ms, wer, path)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (result["run_id"], result.get("suite_id"), result["scenario_id"], result.get("agent_id"), risk,
             result["status"], result["started_at"], result.get("duration_ms"), m.get("latency_p50_ms"),
             m.get("latency_p95_ms"), m.get("wer"), str(path)),
        )


def list_suites(db_path: Path, limit: int = 50) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    with _open(db_path) as con:
        rows = con.execute("SELECT * FROM suites ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = _row(r) or {}
        d.pop("summary_json", None)
        out.append(d)
    return out


def get_suite(db_path: Path, suite_id: str) -> dict[str, Any] | None:
    if not db_path.exists():
        return None
    with _open(db_path) as con:
        r = con.execute("SELECT summary_json FROM suites WHERE suite_id = ?", (suite_id,)).fetchone()
    return json.loads(r["summary_json"]) if r and r["summary_json"] else None


def list_runs(db_path: Path, scenario_id: str | None = None, suite_id: str | None = None,
              limit: int = 100, since: str | None = None) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    q = "SELECT * FROM runs WHERE 1=1"
    args: list[Any] = []
    if scenario_id:
        q += " AND scenario_id = ?"
        args.append(scenario_id)
    if suite_id:
        q += " AND suite_id = ?"
        args.append(suite_id)
    if since:
        q += " AND started_at >= ?"
        args.append(since)
    q += " ORDER BY started_at DESC, run_id DESC LIMIT ?"
    args.append(limit)
    with _open(db_path) as con:
        return [_row(r) or {} for r in con.execute(q, args).fetchall()]


def suite_exists(db_path: Path, suite_id: str) -> bool:
    if not db_path.exists():
        return False
    with _open(db_path) as con:
        return con.execute("SELECT 1 FROM suites WHERE suite_id = ?", (suite_id,)).fetchone() is not None
