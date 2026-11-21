"""MCP server (`pv mcp`): tools called through the SDK's in-process client, with a temp project and the fake agent."""

from __future__ import annotations

import json
import time

import anyio
import pytest
from mcp import Client

from panchovoice.audio.tts import ToneTTS
from panchovoice.llm import FakeProvider
from panchovoice.mcp_server import TOOL_NAMES, build_server
from panchovoice.mocktools import SessionRegistry, start_mock_server

FAST = {"end_of_turn_ms": 500, "first_turn_wait_ms": 700, "transcript_end_ms": 300}

GOOD = """id: quick-hello
title: Quick hello
agent: fake
risk: normal
tags: [smoke]
fixture: acme-clinic.json
caller:
  mode: scripted
  turns:
    - say: "Hi."
limits: {max_turns: 2, max_seconds: 20, silence_timeout_s: 3}
"""

RUN = {
    "run_id": "r_test_1", "suite_id": "s_test", "scenario_id": "quick-hello", "agent_id": "fake",
    "status": "failed", "started_at": "2026-11-18T05:00:00Z", "duration_ms": 9000,
    "metrics": {"latency_p50_ms": 900, "latency_p95_ms": 1200, "wer": 0.05},
    "checks": [{"id": "agent_replied", "kind": "call", "passed": True, "status": "passed", "detail": "ok"},
               {"id": "judge_1", "kind": "judge", "passed": False, "status": "failed",
                "detail": "booked without reading back", "evidence": "Agent: Done, you are booked."}],
    "turns": [{"index": 0, "caller_text": "Hi", "agent_heard": "hi", "agent_text": "Done", "latency_ms": 900}],
    "tool_calls": [{"t_ms": 10, "name": "book_appointment", "args": {"name": "Sam Rivera"}, "response": {"ok": True}}],
    "ended_by": "caller",
}


@pytest.fixture(scope="module")
def mock():
    reg = SessionRegistry()
    srv = start_mock_server(8797, reg)
    yield reg, srv.base_url
    srv.stop()


@pytest.fixture
def make_server(project, fake_agent, mock):
    reg, base = mock

    def make(responder=None, agent_kw=None):
        agent = fake_agent(**(agent_kw or {}))
        cfg = project(agent.url)
        (project.root / "scenarios" / "quick-hello.yaml").write_text(GOOD, encoding="utf-8")
        factory = (lambda: FakeProvider(responder)) if responder else None
        server = build_server(cfg, llm_factory=factory, registry=reg, mock_base_url=base,
                              run_kwargs={"tts": ToneTTS(), "vad": "energy", "stt_fallback": False, "timing": FAST})
        return server, cfg

    return make


def call(server, name, args=None):
    """Call one tool through the in-process MCP client. Returns (is_error, data)."""

    async def go():
        async with Client(server) as c:
            r = await c.call_tool(name, args or {})
            text = "".join(getattr(b, "text", "") for b in r.content)
            if r.is_error:
                return True, text
            data = r.structured_content
            if isinstance(data, dict) and set(data) == {"result"}:
                data = data["result"]
            return False, data if data is not None else json.loads(text)

    return anyio.run(go)


def list_tools(server):
    async def go():
        async with Client(server) as c:
            return (await c.list_tools()).tools

    return anyio.run(go)


def _write_run(cfg, run=RUN):
    d = cfg.runs_path / run["run_id"]
    d.mkdir(parents=True)
    (d / "result.json").write_text(json.dumps(run), encoding="utf-8")


def test_tool_list_has_descriptions_and_schemas(make_server):
    server, _ = make_server()
    tools = {t.name: t for t in list_tools(server)}
    assert set(tools) == set(TOOL_NAMES)
    for t in tools.values():
        assert t.description and len(t.description) > 20, t.name
        assert t.input_schema["type"] == "object"
    assert "confirm" in tools["save_scenario"].input_schema["properties"]
    assert set(tools["get_run"].input_schema["required"]) == {"run_id"}


def test_every_tool_has_all_four_hints_and_a_title(make_server):
    server, _ = make_server()
    tools = {t.name: t for t in list_tools(server)}
    for t in tools.values():
        a = t.annotations
        assert a is not None and t.title, t.name
        for hint in ("read_only_hint", "destructive_hint", "idempotent_hint", "open_world_hint"):
            assert getattr(a, hint) is not None, (t.name, hint)
    read_only = {n for n, t in tools.items() if t.annotations.read_only_hint}
    assert read_only == set(TOOL_NAMES) - {"save_scenario", "run_suite"}
    assert tools["save_scenario"].annotations.destructive_hint is True
    assert tools["run_suite"].annotations.open_world_hint is True
    assert tools["explain_run"].annotations.open_world_hint is True


def test_list_get_validate_scenarios(make_server):
    server, _ = make_server()
    err, rows = call(server, "list_scenarios")
    assert not err and rows == [{"id": "quick-hello", "title": "Quick hello", "risk": "normal", "tags": ["smoke"],
                                 "agent": "fake", "mode": "scripted", "valid": True}]
    assert call(server, "list_scenarios", {"tag": "smoke"})[1][0]["id"] == "quick-hello"
    assert call(server, "list_scenarios", {"tag": "nothing"})[1] == []

    err, one = call(server, "get_scenario", {"id": "quick-hello"})
    assert not err and one["id"] == "quick-hello" and one["yaml"] == GOOD and one["valid"] is True
    err, msg = call(server, "get_scenario", {"id": "nope"})
    assert err and "not found" in msg
    assert call(server, "get_scenario", {"id": "../panchovoice"})[0] is True

    assert call(server, "validate_scenario", {"yaml": GOOD})[1] == {"ok": True, "errors": []}
    err, bad = call(server, "validate_scenario", {"yaml": "id: x\ntitle: t\nagent: fake\ncaller: {mode: ai}\n"})
    assert not err and bad["ok"] is False and {"path", "message"} <= set(bad["errors"][0])


def test_save_scenario_needs_confirm(make_server):
    server, cfg = make_server()
    target = cfg.scenarios_path / "second-one.yaml"
    new = GOOD.replace("quick-hello", "second-one")

    err, preview = call(server, "save_scenario", {"id": "second-one", "yaml": new})
    assert not err and preview["written"] is False and preview["confirm_required"] is True
    assert "confirm=true" in preview["message"] and preview["validation"]["ok"] is True
    assert preview["file"] == "scenarios/second-one.yaml" and preview["exists"] is False
    assert not target.exists()  # the preview writes nothing

    err, done = call(server, "save_scenario", {"id": "second-one", "yaml": new, "confirm": True})
    assert not err and done["written"] is True and done["ok"] is True
    assert target.read_text(encoding="utf-8") == new

    # a change to an existing file shows a diff in the preview
    changed = new.replace("Quick hello", "Second hello")
    err, p2 = call(server, "save_scenario", {"id": "second-one", "yaml": changed})
    assert p2["exists"] is True and "+title: Second hello" in p2["diff"]
    assert target.read_text(encoding="utf-8") == new

    # invalid content is never written, even with confirm
    err, bad = call(server, "save_scenario", {"id": "second-one", "yaml": changed.replace("agent: fake", "agent: ghost"),
                                              "confirm": True})
    assert not err and bad["written"] is False and bad["ok"] is False and bad["errors"][0]["path"] == "agent"
    err, bad_id = call(server, "save_scenario", {"id": "Bad_Id", "yaml": new, "confirm": True})
    assert bad_id["written"] is False and bad_id["errors"][0]["path"] == "id"
    assert target.read_text(encoding="utf-8") == new


def test_runs_and_explain(make_server):
    seen = []

    def responder(system, messages):
        seen.append((system, messages))
        return json.dumps({"reply": "judge_1 failed: the agent booked without reading back.", "proposals": []})

    server, cfg = make_server(responder=responder)
    _write_run(cfg)
    err, run = call(server, "get_run", {"run_id": "r_test_1"})
    assert not err and run["status"] == "failed" and run["metrics"]["latency_p50_ms"] == 900
    assert run["checks"] == {"total": 2, "passed": 1, "not_passed": 1}
    assert run["failed_checks"] == [{"id": "judge_1", "kind": "judge", "status": "failed",
                                     "detail": "booked without reading back", "evidence": "Agent: Done, you are booked."}]
    assert run["turns"][0]["agent_text"] == "Done" and run["tool_calls"][0]["name"] == "book_appointment"
    assert call(server, "get_run", {"run_id": "r_missing"})[0] is True
    assert call(server, "get_run", {"run_id": "..\\x"})[0] is True

    err, ex = call(server, "explain_run", {"run_id": "r_test_1"})
    assert not err and ex["run_id"] == "r_test_1" and "judge_1" in ex["reply"]
    system, messages = seen[0]
    assert "you are booked" in system and "book_appointment" in system
    assert "why" in messages[-1]["content"].lower()
    assert call(server, "explain_run", {"run_id": "r_missing"})[0] is True
    assert call(server, "list_runs")[1] == []  # the hand-written run is not in sqlite


def test_run_suite_end_to_end(make_server):
    server, cfg = make_server(agent_kw={"replies": ["Hello, how can I help?"]})
    err, msg = call(server, "run_suite", {"scenario_ids": ["nope"]})
    assert err and "nope" in msg
    err, started = call(server, "run_suite", {"scenario_ids": ["quick-hello"]})
    assert not err and started["suite_id"].startswith("s_"), started
    sid = started["suite_id"]
    err, busy = call(server, "run_suite", {"tags": ["smoke"]})
    assert err and "already running" in busy

    deadline = time.monotonic() + 60
    while True:
        err, s = call(server, "get_suite", {"suite_id": sid})
        assert not err
        if s["status"] != "running":
            break
        assert time.monotonic() < deadline, "suite did not finish"
        time.sleep(0.3)
    assert s["status"] == "finished" and s["passed"] == 1 and s["pass_rate"] == 1.0 and s["gate_passed"] is True
    assert len(s["runs"]) == 1 and s["runs"][0]["scenario_id"] == "quick-hello"
    assert set(s["runs"][0]) >= {"run_id", "status", "latency_p50_ms", "latency_p95_ms", "wer", "failed_checks"}

    err, runs = call(server, "list_runs", {"limit": 5, "scenario_id": "quick-hello"})
    assert not err and len(runs) == 1 and "path" not in runs[0]
    err, run = call(server, "get_run", {"run_id": runs[0]["run_id"]})
    assert not err and run["status"] == "passed" and run["turns"]

    err, g = call(server, "gate_status", {"suite_id": sid})
    assert not err and g == {**g, "suite_id": sid, "status": "finished", "pass_rate": 1.0, "gate_passed": True,
                             "reasons": [], "threshold": 0.95}
    err, latest = call(server, "gate_status")
    assert not err and latest["suite_id"] == sid
    assert call(server, "get_suite", {"suite_id": "s_missing"})[0] is True


def test_gate_status_reasons(make_server):
    from panchovoice import db

    server, cfg = make_server()
    assert call(server, "gate_status")[0] is True  # no suites yet
    db.upsert_suite(cfg.db_path, {
        "suite_id": "s_20261118_000000", "started_at": "2026-11-18T00:00:00Z", "finished_at": "2026-11-18T00:01:00Z",
        "status": "done", "total": 2, "done": 2, "passed": 1, "failed": 1, "errors": 0, "pass_rate": 0.5,
        "gate_passed": False, "gate_detail": "pass rate 50.00% < 95%; critical scenarios not passed: book",
        "runs": [{"run_id": "r_a", "scenario_id": "book", "risk": "critical", "status": "failed"},
                 {"run_id": "r_b", "scenario_id": "hello", "risk": "normal", "status": "passed"}]})
    err, g = call(server, "gate_status")
    assert not err and g["gate_passed"] is False and g["pass_rate"] == 0.5
    assert g["reasons"] == ["pass rate 50.00% < 95%", "critical scenarios not passed: book"]


def test_cli_has_mcp_command():
    from typer.testing import CliRunner

    from panchovoice.cli import app

    r = CliRunner().invoke(app, ["mcp", "--help"])
    assert r.exit_code == 0 and "--config" in r.output
