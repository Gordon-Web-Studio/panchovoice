"""MCP server of PanchoVoiceAI (`pv mcp`): lets a coding agent list and edit scenarios, run suites and read results.

Transport is stdio. The only tool that writes a file is `save_scenario`, and only with `confirm=true`.
"""

from __future__ import annotations

import difflib
import json
import re
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable

import yaml
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import __version__, db, onboarding, panchito
from .checks import gate
from .config import Config
from .errors import FieldError
from .llm import LLMProvider
from .mocktools import SessionRegistry, start_mock_server
from .scenario import KEBAB, check_scenario_refs, list_scenario_files, validate_scenario_text
from .server.suites import SuiteBusy, SuiteManager

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
RECENT_RUNNING_S = 600  # a suite file touched this recently by another process counts as running

TOOL_NAMES = ["list_scenarios", "get_scenario", "validate_scenario", "save_scenario", "run_suite", "get_suite",
              "list_runs", "get_run", "explain_run", "gate_status"]

INSTRUCTIONS = """\
PanchoVoiceAI tests voice AI agents: a caller talks with real audio to the agent under test, both channels are
recorded, then each call (a run) is scored with checks. A suite is a batch of runs; the gate is the go or no go rule.
Typical flow: list_scenarios, run_suite, poll get_suite until status is not "running", then get_run or explain_run
on failed runs, and gate_status for the verdict.
Rule: save_scenario writes nothing unless confirm=true. Call it first without confirm, show the preview and the diff
to the user, and call again with confirm=true only after the user says yes.
"""

EXPLAIN_DEFAULT = ("Why did this run fail? Name the failed checks, quote the evidence, and suggest one concrete fix. "
                   "If it passed, say what it proved.")


def _free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _short(v: Any, n: int) -> Any:
    if isinstance(v, str) and len(v) > n:
        return v[: n - 3] + "..."
    return v


def _errors(errs: list[FieldError]) -> list[dict[str, str]]:
    return [e.to_dict() for e in errs]


def _suite_status(s: str | None) -> str:
    return "finished" if s == "done" else (s or "running")


def _check_status(c: dict[str, Any]) -> str:
    return c.get("status") or ("passed" if c.get("passed") else "failed")


def compact_run(r: dict[str, Any]) -> dict[str, Any]:
    """The parts of result.json a coding agent needs: status, metrics, failed checks with evidence, turns, tools."""
    checks = r.get("checks") or []
    bad = [c for c in checks if _check_status(c) != "passed"]
    return {
        "run_id": r.get("run_id"), "suite_id": r.get("suite_id"), "scenario_id": r.get("scenario_id"),
        "title": r.get("title"), "agent_id": r.get("agent_id"), "risk": r.get("risk"), "status": r.get("status"),
        "error": r.get("error"), "started_at": r.get("started_at"), "duration_ms": r.get("duration_ms"),
        "ended_by": r.get("ended_by"), "metrics": r.get("metrics") or {},
        "checks": {"total": len(checks), "passed": len(checks) - len(bad), "not_passed": len(bad)},
        "failed_checks": [{"id": c.get("id"), "kind": c.get("kind"), "status": _check_status(c),
                           "detail": _short(c.get("detail", ""), 600),
                           **({"evidence": _short(c["evidence"], 600)} if c.get("evidence") else {})} for c in bad],
        "passed_checks": [c.get("id") for c in checks if c not in bad] or None,
        "turns": [{k: _short(t.get(k), 400) for k in ("index", "caller_text", "agent_heard", "agent_text",
                                                        "latency_ms", "interrupted", "stop_ms") if k in t}
                  for t in (r.get("turns") or [])[:30]],
        "tool_calls": [{"t_ms": c.get("t_ms"), "name": c.get("name"), "args": c.get("args"),
                        "response": c.get("response")} for c in (r.get("tool_calls") or [])[:30]],
        "final_state": r.get("final_state"),
    }


def build_server(
    config: Config,
    *,
    llm_factory: Callable[[], LLMProvider] | None = None,
    registry: SessionRegistry | None = None,
    mock_base_url: str | None = None,
    mock_port: int = 0,
    run_kwargs: dict[str, Any] | None = None,
) -> MCPServer:
    """Build the MCP server. Without `mock_base_url`, the first run_suite starts a mock tools server on
    `mock_port` (0 means a free port, so it never collides with `pv serve` on 8700)."""
    server = MCPServer(name="panchovoice", title="PanchoVoiceAI", version=__version__, instructions=INSTRUCTIONS)
    reg = registry or SessionRegistry()
    kwargs: dict[str, Any] = {"registry": reg, **(run_kwargs or {})}
    if llm_factory and "llm_factory" not in kwargs:
        kwargs["llm_factory"] = llm_factory
    suites = SuiteManager(config, kwargs)
    mock_state: dict[str, Any] = {"url": mock_base_url, "server": None}
    mock_lock = threading.Lock()

    def ensure_mock() -> str:
        with mock_lock:
            if not mock_state["url"]:
                srv = start_mock_server(mock_port or _free_port(), reg)
                mock_state.update(url=srv.base_url, server=srv)
            suites.run_kwargs["mock_base_url"] = mock_state["url"]
            return mock_state["url"]

    # ------------------------------------------------------------------ helpers

    def safe_id(value: str, what: str) -> str:
        if not ID_RE.match(value or "") or ".." in value:
            raise ToolError(f"invalid {what}: {value!r}")
        return value

    def scenario_file(sid: str) -> Path | None:
        for p in list_scenario_files(config.scenarios_path):
            if p.stem == sid:
                return p
        return None

    def rel(p: Path) -> str:
        try:
            return p.resolve().relative_to(config.root.resolve()).as_posix()
        except ValueError:
            return str(p)

    def validate_text(text: str, filename: str | None) -> list[FieldError]:
        scn, errs = validate_scenario_text(text, filename)
        if scn is not None and not errs:
            errs = check_scenario_refs(scn, config)
        return errs

    def read_run(run_id: str) -> dict[str, Any]:
        f = config.runs_path / safe_id(run_id, "run id") / "result.json"
        if not f.is_file():
            raise ToolError(f"run {run_id} not found (see list_runs)")
        return json.loads(f.read_text(encoding="utf-8"))

    def other_process_running() -> str | None:
        """A suite that another process (for example `pv serve`) runs right now, by sqlite plus a fresh suite file."""
        for row in db.list_suites(config.db_path, 5):
            if row.get("status") != "running" or row["suite_id"] in suites.memory:
                continue
            f = config.data_path / "suites" / f"{row['suite_id']}.json"
            if f.is_file() and time.time() - f.stat().st_mtime < RECENT_RUNNING_S:
                return row["suite_id"]
        return None

    # ------------------------------------------------------------------ scenarios

    @server.tool()
    def list_scenarios(tag: str | None = None) -> list[dict[str, Any]]:
        """List the test scenarios of this project (one YAML file each in scenarios_dir).

        Each row has id, title, risk (critical or normal), tags, agent (agent id), mode (scripted or ai) and valid.
        Pass `tag` to keep only scenarios with that tag.
        """
        out = []
        for p in list_scenario_files(config.scenarios_path):
            text = p.read_text(encoding="utf-8")
            try:
                raw = yaml.safe_load(text)
            except yaml.YAMLError:
                raw = None
            raw = raw if isinstance(raw, dict) else {}
            tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
            if tag and tag not in tags:
                continue
            caller = raw.get("caller") if isinstance(raw.get("caller"), dict) else {}
            out.append({"id": p.stem, "title": str(raw.get("title") or p.stem), "risk": raw.get("risk") or "normal",
                        "tags": tags, "agent": raw.get("agent") or "", "mode": caller.get("mode") or "scripted",
                        "valid": not validate_text(text, p.name)})
        return out

    @server.tool()
    def get_scenario(id: str) -> dict[str, Any]:
        """Get one scenario: its full YAML text, file path, and validation errors (if any)."""
        p = scenario_file(safe_id(id, "scenario id"))
        if p is None:
            raise ToolError(f"scenario {id} not found (see list_scenarios)")
        text = p.read_text(encoding="utf-8")
        errs = validate_text(text, p.name)
        return {"id": id, "yaml": text, "file": rel(p), "valid": not errs, "errors": _errors(errs)}

    @server.tool()
    def validate_scenario(yaml: str) -> dict[str, Any]:
        """Check scenario YAML text without saving it. Returns {ok, errors: [{path, message}]}.

        It checks the schema, and that the agent id and the fixture exist in this project.
        """
        errs = validate_text(yaml, None)
        return {"ok": not errs, "errors": _errors(errs)}

    @server.tool()
    def save_scenario(id: str, yaml: str, confirm: bool = False) -> dict[str, Any]:
        """Create or replace scenario `id` (file scenarios_dir/<id>.yaml). The id must be kebab-case and equal the
        `id:` key in the YAML.

        Confirm before change: with confirm=false (the default) NOTHING is written; you get a preview with the
        validation result and a diff against the current file. Show it to the user, and call again with
        confirm=true only after the user agrees. With confirm=true the YAML is validated again and written only
        when it is valid.
        """
        if not re.match(KEBAB, id or ""):
            return {"written": False, "ok": False,
                    "errors": [{"path": "id", "message": f"id '{id}' must be kebab-case"}]}
        errs = validate_text(yaml, f"{id}.yaml")
        existing = scenario_file(id)
        path = existing or (config.scenarios_path / f"{id}.yaml")
        text = yaml if yaml.endswith("\n") else yaml + "\n"
        if not confirm:
            old = existing.read_text(encoding="utf-8") if existing else ""
            diff = "".join(difflib.unified_diff(old.splitlines(keepends=True), text.splitlines(keepends=True),
                                                fromfile=f"a/{rel(path)}", tofile=f"b/{rel(path)}"))
            msg = ("Nothing written. Show this preview to the user, then call again with confirm=true to write."
                   if not errs else "Nothing written. The YAML is not valid; fix the errors first.")
            return {"written": False, "confirm_required": True, "file": rel(path), "exists": existing is not None,
                    "validation": {"ok": not errs, "errors": _errors(errs)}, "diff": diff, "message": msg}
        if errs:
            return {"written": False, "ok": False, "errors": _errors(errs)}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        try:
            onboarding.mark(config, "scenario_created")
        except OSError:
            pass
        return {"written": True, "ok": True, "errors": [], "file": rel(path)}

    # ------------------------------------------------------------------ suites

    @server.tool()
    def run_suite(scenario_ids: list[str] | None = None, tags: list[str] | None = None,
                  runs: int | None = None) -> dict[str, Any]:
        """Start a suite in the background and return {suite_id} at once. Poll get_suite until status is not
        "running" (each call takes about 20 s to 3 min of real audio).

        With no arguments every valid scenario runs. `scenario_ids` picks scenarios, `tags` keeps scenarios that
        have any of these tags, `runs` repeats each scenario N times (default: the scenario's own `runs`).
        Only one suite runs at a time; a second call while one runs is refused.
        """
        if runs is not None and runs < 1:
            raise ToolError("runs must be 1 or more")
        if suites.busy():
            raise ToolError(f"A suite is already running ({suites.active_id}). Wait for it to finish "
                             "(see get_suite), then start again.")
        other = other_process_running()
        if other:
            raise ToolError(f"A suite is already running in another PanchoVoiceAI process ({other}). "
                             "Wait for it to finish, then start again.")
        ensure_mock()
        try:
            sid = suites.start(scenario_ids or None, tags or None, None, runs)
        except (SuiteBusy, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        mem = suites.memory.get(sid) or {}
        return {"suite_id": sid, "status": "running", "total": mem.get("total"),
                "scenario_ids": mem.get("scenario_ids"), "next": "poll get_suite with this suite_id"}

    def load_suite(suite_id: str) -> dict[str, Any]:
        s = suites.get(safe_id(suite_id, "suite id"))
        if s is None:
            raise ToolError(f"suite {suite_id} not found")
        return s

    @server.tool()
    def get_suite(suite_id: str) -> dict[str, Any]:
        """Get a suite: status (running, finished, error, interrupted), progress (done of total), passed, failed,
        errors, pass_rate, gate_passed, gate_detail, and one row per run (status, latency p50 and p95 in ms,
        wer, failed check ids, harness error)."""
        s = load_suite(suite_id)
        keep = ("suite_id", "status", "started_at", "finished_at", "agent_id", "total", "done", "passed", "failed",
                "errors", "pass_rate", "gate_passed", "gate_detail", "scenario_ids", "skipped_invalid", "current",
                "error")
        out = {k: s.get(k) for k in keep if k in s}
        out["status"] = _suite_status(s.get("status"))
        out["runs"] = [{k: r.get(k) for k in ("run_id", "scenario_id", "risk", "status", "latency_p50_ms",
                                              "latency_p95_ms", "wer", "duration_ms", "failed_checks", "error")}
                       for r in (s.get("runs") or []) if r]
        return out

    @server.tool()
    def gate_status(suite_id: str | None = None) -> dict[str, Any]:
        """The go or no go verdict of a suite (default: the newest suite): pass_rate, gate_passed and the reasons
        it failed. Errors (harness could not measure) are left out of the pass rate but fail the gate."""
        if suite_id is None:
            candidates = [s for s in suites.memory.values()]
            rows = db.list_suites(config.db_path, 1)
            ids = [r["suite_id"] for r in rows] + [c["suite_id"] for c in candidates]
            if not ids:
                raise ToolError("no suites yet; start one with run_suite")
            suite_id = max(ids)  # ids are s_YYYYMMDD_HHMMSS, so the newest sorts last
        s = load_suite(suite_id)
        status = _suite_status(s.get("status"))
        runs = [r for r in (s.get("runs") or []) if r]
        if runs and all("status" in r and "scenario_id" in r for r in runs):
            g = gate(runs, config.gate)
        else:
            g = {k: s.get(k) for k in ("passed", "failed", "errors", "pass_rate", "gate_passed")}
            g["gate_detail"] = s.get("gate_detail") or ""
        detail = g.get("gate_detail") or ""
        reasons = [] if g.get("gate_passed") or detail in ("", "gate passed") else detail.split("; ")
        out = {"suite_id": suite_id, "status": status, "pass_rate": g.get("pass_rate"),
               "gate_passed": g.get("gate_passed") if status != "running" else None, "reasons": reasons,
               "passed": g.get("passed"), "failed": g.get("failed"), "errors": g.get("errors"),
               "done": s.get("done"), "total": s.get("total"), "threshold": config.gate.pass_rate,
               "critical_must_pass_all": config.gate.critical_must_pass_all}
        if status == "running":
            out["note"] = "the suite is still running; the verdict is final when status is finished"
        return out

    # ------------------------------------------------------------------ runs

    @server.tool()
    def list_runs(limit: int = 20, scenario_id: str | None = None) -> list[dict[str, Any]]:
        """List recent runs, newest first: run_id, suite_id, scenario_id, status (passed, failed, error),
        started_at, duration_ms, latency_p50_ms, latency_p95_ms, wer. `limit` is 1 to 200."""
        rows = db.list_runs(config.db_path, scenario_id=scenario_id or None, limit=max(1, min(int(limit), 200)))
        for r in rows:
            r.pop("path", None)
        return rows

    @server.tool()
    def get_run(run_id: str) -> dict[str, Any]:
        """One run result in compact form: status, harness error, metrics (latency p50 and p95 in ms, wer,
        barge-in stop time), check counts, failed checks with their evidence, turns (what the caller said, what
        the agent heard and said, latency), mock tool calls and the final mock state."""
        return compact_run(read_run(run_id))

    @server.tool()
    def explain_run(run_id: str, question: str | None = None) -> dict[str, Any]:
        """Ask Panchito (the PanchoVoiceAI helper, uses the project's LLM) to explain a run: why it failed and one
        concrete fix. Pass `question` to ask something else about the run. Can take up to 2 minutes.
        Returns {run_id, reply, proposals}; proposals are never saved (use save_scenario for that)."""
        read_run(run_id)  # not found -> error before calling the LLM
        msgs = [{"role": "user", "content": question or EXPLAIN_DEFAULT}]
        res = panchito.chat(config, msgs, {"run_id": run_id, "page": "mcp"}, provider_factory=llm_factory)
        out = {"run_id": run_id, "reply": res.get("reply", ""), "proposals": res.get("proposals") or []}
        if res.get("error"):
            out["error"] = res["error"]
        return out

    server.panchovoice_suites = suites  # type: ignore[attr-defined]  (for tests and debugging)
    return server


def main(config_path: str | None = None, mock_port: int = 0) -> int:
    """Entry of `pv mcp`: serve over stdio until the client closes stdin."""
    from .config import load_config

    config = load_config(config_path)
    build_server(config, mock_port=mock_port).run("stdio")
    return 0
