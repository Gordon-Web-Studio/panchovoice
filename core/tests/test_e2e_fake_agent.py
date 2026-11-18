"""Adapter + runner end to end against the fake pv-ws/1 agent (real WebSocket, real time pacing)."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest

from panchovoice import db
from panchovoice.audio.pcm import read_wav
from panchovoice.audio.tts import ToneTTS
from panchovoice.llm import FakeProvider
from panchovoice.mocktools import SessionRegistry, start_mock_server
from panchovoice.report import junit_xml
from panchovoice.runner import run_scenario, run_suite
from panchovoice.scenario import validate_scenario_text

FAST = {"end_of_turn_ms": 500, "first_turn_wait_ms": 700, "transcript_end_ms": 300}


@pytest.fixture(scope="module")
def mock():
    reg = SessionRegistry()
    srv = start_mock_server(8798, reg)
    yield reg, srv.base_url
    srv.stop()


def _scenario(body: str, sid: str = "fake-run"):
    scn, errors = validate_scenario_text(f"id: {sid}\ntitle: fake\nagent: fake\nfixture: acme-clinic.json\n" + body)
    assert not errors, errors
    return scn


SCRIPTED = """
risk: critical
caller:
  mode: scripted
  turns:
    - say: "Hi, I need Tuesday at ten."
    - say: "Sam Rivera."
expect:
  tool_calls:
    - {name: find_slot, args: {day: tuesday, time: "10:00"}}
    - {name: book_appointment, args: {name: Sam Rivera}}
  order: [find_slot, book_appointment]
  final_state:
    appointments: [{name: Sam Rivera, day: tuesday, time: "10:00"}]
  latency_ms: {p50: 1500, p95: 2000}
  wer_max: 0.3
  must_say: ["booked"]
  never_say: ["system prompt"]
  judge:
    - "The agent confirmed the booking."
limits: {max_turns: 4, max_seconds: 30, silence_timeout_s: 4}
"""


def test_scripted_run_end_to_end(project, fake_agent, mock):
    reg, base = mock
    agent = fake_agent(
        reply_delay_ms=400, reply_ms=700,
        heard=["hi i need tuesday at 10", "sam rivera"],
        replies=["Tuesday at 10 is free. Your name?", "You are booked, Sam."],
        tool_calls={0: ("find_slot", {"day": "Tuesday", "time": "10 am"}),
                    1: ("book_appointment", {"name": "Sam Rivera", "day": "tuesday", "time": "10"})},
    )
    cfg = project(agent.url)
    judge = FakeProvider(lambda s, m: '{"answers": [{"question": "q", "passed": true, "evidence": "You are booked, Sam."}]}')
    events = []
    res = run_scenario(_scenario(SCRIPTED), cfg, llm=judge, tts=ToneTTS(), registry=reg, mock_base_url=base,
                       vad="energy", stt_fallback=False, timing=FAST, on_event=events.append)
    assert res["status"] == "passed", json.dumps(res["checks"], indent=1) + str(res["error"]) + str(res["notes"])

    # start message per contract section 5
    start = agent.starts[0]
    assert start["protocol"] == "pv-ws/1" and start["sample_rate"] == 16000
    assert start["mock_tools_url"].endswith(start["session"])

    turns = res["turns"]
    assert len(turns) == 2
    for t in turns:
        # the agent waits ~250 ms of silence then 400 ms delay: latency about 650 ms (+ frame and network jitter)
        assert 500 <= t["latency_ms"] <= 1100, t
        assert t["agent_start_ms"] - t["caller_end_ms"] == t["latency_ms"]
        assert t["caller_end_ms"] > t["caller_start_ms"]
    assert turns[0]["agent_heard"] == "hi i need tuesday at 10"
    assert turns[1]["agent_text"] == "You are booked, Sam."
    assert res["metrics"]["wer"] == 0.0
    assert res["metrics"]["turn_count"] == 2
    assert [c["name"] for c in res["tool_calls"]] == ["find_slot", "book_appointment"]
    assert res["tool_calls"][0]["t_ms"] > turns[0]["caller_end_ms"]
    assert res["final_state"]["appointments"] == [{"name": "Sam Rivera", "day": "tuesday", "time": "10:00"}]
    judge_check = next(c for c in res["checks"] if c["kind"] == "judge")
    assert judge_check["evidence"] == "You are booked, Sam."
    assert res["cost"]["llm_calls"] == 1

    # files: stereo wav with caller left and agent right on one timeline
    run_dir = cfg.runs_path / res["run_id"]
    stereo, sr = read_wav(run_dir / "stereo.wav")
    assert sr == 16000 and stereo.shape[1] == 2
    caller, _ = read_wav(run_dir / "caller.wav")
    agent_ch, _ = read_wav(run_dir / "agent.wav")
    assert (stereo[:, 0] == caller).all() and (stereo[:, 1] == agent_ch).all()
    c0 = turns[0]["caller_start_ms"] * 16
    assert abs(caller[c0 + 800: c0 + 1600].astype(int)).max() > 1000
    assert abs(agent_ch[c0 + 800: c0 + 1600].astype(int)).max() == 0
    a0 = turns[0]["agent_start_ms"] * 16
    assert abs(agent_ch[a0: a0 + 1600]).max() > 1000
    on_disk = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert on_disk["run_id"] == res["run_id"] and on_disk["status"] == "passed"
    tx = json.loads((run_dir / "transcript.json").read_text(encoding="utf-8"))
    assert [l["role"] for l in tx["lines"]] == ["caller", "agent", "caller", "agent"]
    assert any(e["type"] == "caller_turn" for e in events)
    # sqlite index
    rows = db.list_runs(cfg.db_path)
    assert rows[0]["run_id"] == res["run_id"] and rows[0]["latency_p50_ms"] == res["metrics"]["latency_p50_ms"]


def test_planted_style_failure_and_judge_down(project, fake_agent, mock):
    reg, base = mock
    agent = fake_agent(tool_calls={0: ("cancel_appointment", {"name": "Sam Rivera", "day": "tuesday"})})
    cfg = project(agent.url)
    body = """
caller: {mode: scripted, turns: [{say: "Hello there."}]}
expect:
  forbidden_tools: [cancel_appointment]
  judge: ["The agent was polite."]
limits: {max_turns: 2, max_seconds: 20, silence_timeout_s: 3}
"""
    res = run_scenario(_scenario(body), cfg, llm=FakeProvider(fail=True), tts=ToneTTS(), registry=reg,
                       mock_base_url=base, vad="energy", stt_fallback=False, timing=FAST)
    by = {c["id"]: c for c in res["checks"]}
    assert by["forbidden_tools"]["status"] == "failed"
    assert by["judge_1"]["status"] == "error" and by["judge_1"]["passed"] is None
    assert res["status"] == "failed"


def test_barge_in_stop_time(project, fake_agent, mock):
    reg, base = mock
    agent = fake_agent(reply_delay_ms=300, long_reply_turns={0}, stop_delay_ms=200)
    cfg = project(agent.url)
    body = """
caller:
  mode: scripted
  turns:
    - say: "Tell me all the free slots."
    - say: "Stop, just Tuesday."
      interrupt_after_ms: 800
expect:
  barge_in: {max_stop_ms: 1000}
limits: {max_turns: 3, max_seconds: 30, silence_timeout_s: 4}
"""
    res = run_scenario(_scenario(body), cfg, tts=ToneTTS(), registry=reg, mock_base_url=base,
                       vad="energy", stt_fallback=False, timing=FAST)
    assert res["status"] == "passed", res["checks"]
    t1 = res["turns"][1]
    assert t1["interrupted"] is True
    # second caller turn starts ~800 ms after the agent onset
    assert 700 <= t1["caller_start_ms"] - res["turns"][0]["agent_start_ms"] <= 1000
    # agent reacts after ~200 ms (plus one frame of energy detection on its side)
    assert 100 <= t1["stop_ms"] <= 600, t1
    assert res["metrics"]["barge_in_stop_ms"] == t1["stop_ms"]
    assert agent.clears == 1


def test_greeting_pause_and_agent_end(project, fake_agent, mock):
    reg, base = mock
    agent = fake_agent(greet_ms=800, send_end_after_turns=1)
    cfg = project(agent.url)
    body = """
caller:
  mode: scripted
  turns:
    - say: "Hi."
      pause_ms: 1000
    - say: "Never said, the agent ends the call first."
limits: {max_turns: 3, max_seconds: 20, silence_timeout_s: 3}
"""
    res = run_scenario(_scenario(body), cfg, tts=ToneTTS(), registry=reg, mock_base_url=base,
                       vad="energy", stt_fallback=False, timing=FAST)
    assert res["status"] == "passed", res
    assert res["ended_by"] == "agent"
    assert len(res["turns"]) == 1
    # greeting 800 ms + quiet 300 ms (agent transcript seen, audio drained) + pause 1000 ms: about 2.1 s
    assert res["turns"][0]["caller_start_ms"] >= 2050
    tx = json.loads((cfg.runs_path / res["run_id"] / "transcript.json").read_text(encoding="utf-8"))
    assert tx["lines"][0] == {"t_ms": 0, "role": "agent", "text": "Hello from the fake agent.", "source": "agent"}


def test_ai_caller_end_to_end(project, fake_agent, mock):
    reg, base = mock
    agent = fake_agent(replies=["Which day?", "Booked for Tuesday."],
                       tool_calls={1: ("book_appointment", {"name": "Sam Rivera", "day": "tuesday", "time": "15:30"})})
    cfg = project(agent.url)
    body = """
caller:
  mode: ai
  persona: Sam
  goal: Book Tuesday 3:30 PM
  facts: {name: Sam Rivera}
  opening_line: "Hi, I need an appointment."
expect:
  tool_calls: [{name: book_appointment, args: {day: tuesday, time: "3:30 pm"}}]
limits: {max_turns: 4, max_seconds: 30, silence_timeout_s: 4}
"""
    seen = []

    def responder(system, messages):
        seen.append(messages[0]["content"])
        if "Booked" in messages[0]["content"]:
            return '{"say": "Great, bye.", "end_call": true}'
        return '{"say": "Tuesday at 3:30 PM, Sam Rivera.", "end_call": false}'

    res = run_scenario(_scenario(body), cfg, llm=FakeProvider(responder), tts=ToneTTS(), registry=reg,
                       mock_base_url=base, vad="energy", stt_fallback=False, timing=FAST)
    assert res["status"] == "passed", res
    assert [t["caller_text"] for t in res["turns"]] == ["Hi, I need an appointment.", "Tuesday at 3:30 PM, Sam Rivera.", "Great, bye."]
    assert "AGENT: Which day?" in seen[0]
    assert res["cost"]["llm_calls"] == 2


def test_agent_down_is_error_not_failure(project, mock):
    reg, base = mock
    cfg = project("ws://127.0.0.1:9/ws")
    res = run_scenario(_scenario('caller: {mode: scripted, turns: [{say: "Hi."}]}\n'), cfg, tts=ToneTTS(),
                       registry=reg, mock_base_url=base, vad="energy", stt_fallback=False, timing=FAST)
    assert res["status"] == "error"
    assert "cannot connect" in res["error"]
    assert (cfg.runs_path / res["run_id"] / "result.json").exists()


def test_run_suite_gate_and_junit(project, fake_agent, mock):
    reg, base = mock
    agent = fake_agent()
    cfg = project(agent.url)
    root = project.root
    (root / "scenarios" / "s-pass.yaml").write_text(
        "id: s-pass\ntitle: p\nagent: fake\nrisk: normal\ntags: [x]\n"
        "caller: {mode: scripted, turns: [{say: Hi.}]}\nlimits: {max_turns: 2, max_seconds: 20, silence_timeout_s: 3}\n",
        encoding="utf-8")
    (root / "scenarios" / "s-crit.yaml").write_text(
        "id: s-crit\ntitle: c\nagent: fake\nrisk: critical\ntags: [x]\nfixture: acme-clinic.json\n"
        "caller: {mode: scripted, turns: [{say: Hi.}]}\n"
        "expect: {tool_calls: [{name: book_appointment}]}\nlimits: {max_turns: 2, max_seconds: 20, silence_timeout_s: 3}\n",
        encoding="utf-8")
    (root / "scenarios" / "s-bad.yaml").write_text("id: s-bad\ntitle: b\n", encoding="utf-8")
    events = []
    suite = run_suite(cfg, tags=["x"], runs=2, on_progress=events.append, tts=ToneTTS(), registry=reg,
                      mock_base_url=base, vad="energy", stt_fallback=False, timing=FAST)
    results = suite.pop("results")
    assert suite["total"] == 4 and suite["done"] == 4 and suite["status"] == "done"
    assert suite["passed"] == 2 and suite["failed"] == 2 and suite["pass_rate"] == 0.5
    assert suite["gate_passed"] is False and "s-crit" in suite["gate_detail"]
    assert suite["skipped_invalid"] == ["s-bad.yaml"]
    types = [e["type"] for e in events]
    assert types[0] == "suite_started" and types[-1] == "suite_finished" and types.count("run_finished") == 4
    stored = db.get_suite(cfg.db_path, suite["suite_id"])
    assert stored["gate_passed"] is False and len(stored["runs"]) == 4
    assert db.list_suites(cfg.db_path)[0]["suite_id"] == suite["suite_id"]
    root_xml = ET.fromstring(junit_xml(suite, results))
    assert root_xml.find("testsuite").get("failures") == "2"


def test_cli_run_gate_exit_code(project, fake_agent, monkeypatch, tmp_path):
    """`pv run --gate --junit` against the fake agent: exits 1 when the gate fails and writes JUnit XML."""
    from typer.testing import CliRunner

    import panchovoice.runner as runner_mod
    from panchovoice.cli import app

    agent = fake_agent()
    cfg = project(agent.url)
    root = project.root
    (root / "scenarios" / "c-fail.yaml").write_text(
        "id: c-fail\ntitle: c\nagent: fake\nrisk: critical\nfixture: acme-clinic.json\n"
        "caller: {mode: scripted, turns: [{say: Hi.}]}\n"
        "expect: {tool_calls: [{name: book_appointment}]}\nlimits: {max_turns: 2, max_seconds: 20, silence_timeout_s: 3}\n",
        encoding="utf-8")
    real = runner_mod.run_scenario

    def fast_run(*a, **kw):
        kw.update(tts=ToneTTS(), vad="energy", stt_fallback=False, timing=FAST)
        return real(*a, **kw)

    monkeypatch.setattr(runner_mod, "run_scenario", fast_run)
    out = tmp_path / "junit.xml"
    r = CliRunner().invoke(app, ["--config", str(cfg.source), "run", "--gate", "--junit", str(out), "--mock-port", "8797"])
    assert r.exit_code == 1, r.output
    assert out.exists() and "c-fail" in out.read_text(encoding="utf-8")
