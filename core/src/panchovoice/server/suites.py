"""Runs one suite at a time in a background thread for POST /api/suites."""

from __future__ import annotations

import threading
import traceback
from typing import Any

from .. import db
from ..config import Config
from ..runner import iso, new_suite_id, run_suite, select_scenarios, utc_now


class SuiteBusy(RuntimeError):
    pass


class SuiteManager:
    def __init__(self, config: Config, run_kwargs: dict[str, Any] | None = None):
        self.config = config
        self.run_kwargs = dict(run_kwargs or {})
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.active_id: str | None = None
        # in memory view of the active (or last) suite, used until sqlite has the row
        self.memory: dict[str, dict[str, Any]] = {}

    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, scenario_ids: list[str] | None = None, tags: list[str] | None = None,
              agent_id: str | None = None, runs: int | None = None) -> str:
        """Validate the selection now (ValueError on bad input), then run in the background."""
        with self._lock:
            if self.busy():
                raise SuiteBusy(f"A suite is already running ({self.active_id}). Wait for it to finish, then start again.")
            chosen, _invalid = select_scenarios(self.config, scenario_ids or None, tags or None, agent_id or None)
            if not chosen:
                raise ValueError("No scenarios match this selection.")
            sid = new_suite_id(self.config)
            total = sum(runs or s.runs for s in chosen)
            self.memory[sid] = {"suite_id": sid, "started_at": iso(utc_now()), "status": "running", "total": total,
                                "done": 0, "passed": 0, "failed": 0, "errors": 0, "pass_rate": None,
                                "gate_passed": None, "runs": [], "scenario_ids": [s.id for s in chosen]}
            self.active_id = sid
            self._thread = threading.Thread(target=self._run, args=(sid, chosen, agent_id, runs),
                                            name=f"pv-suite-{sid}", daemon=True)
            self._thread.start()
            return sid

    def _on_progress(self, sid: str, e: dict[str, Any]) -> None:
        mem = self.memory.get(sid)
        if mem is None:
            return
        t = e.get("type")
        if t == "suite_progress":
            mem["done"] = e.get("done", mem["done"])
            mem["runs"].append(e.get("last"))
        elif t == "run_started":
            mem["current"] = {"scenario_id": e.get("scenario_id"), "index": e.get("index")}

    def _run(self, sid: str, chosen, agent_id, runs) -> None:
        try:
            run_suite(self.config, scenarios=chosen, agent_id=agent_id, runs=runs, suite_id=sid,
                      on_progress=lambda e: self._on_progress(sid, e), **self.run_kwargs)
            self.memory[sid]["status"] = "done"
        except Exception as exc:  # harness crash: record it so polling stops
            mem = self.memory[sid]
            mem["status"] = "error"
            mem["error"] = f"{type(exc).__name__}: {exc}"
            mem["trace"] = traceback.format_exc(limit=5)
            stored = db.get_suite(self.config.db_path, sid)
            if stored:
                stored.update({"status": "error", "error": mem["error"], "finished_at": iso(utc_now())})
                db.upsert_suite(self.config.db_path, stored)
        finally:
            self.memory[sid].pop("current", None)

    def get(self, sid: str) -> dict[str, Any] | None:
        stored = db.get_suite(self.config.db_path, sid)
        mem = self.memory.get(sid)
        if stored is None:
            return dict(mem) if mem else None
        if mem and mem.get("current") and stored.get("status") == "running":
            stored["current"] = mem["current"]
        if stored.get("status") == "running" and not (sid == self.active_id and self.busy()):
            stored["status"] = "interrupted"  # the process that ran it stopped
        return stored
