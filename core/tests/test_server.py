"""API server (contract section 7) with FastAPI TestClient, a temp data_dir, the fake agent and a FakeProvider."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from panchovoice import panchito
from panchovoice.audio.tts import ToneTTS
from panchovoice.llm import FakeProvider
from panchovoice.mocktools import SessionRegistry, start_mock_server
from panchovoice.server import create_app

FAST = {"end_of_turn_ms": 500, "first_turn_wait_ms": 700, "transcript_end_ms": 300}

GOOD_SCENARIO = """id: quick-hello
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


@pytest.fixture(scope="module")
def mock():
    reg = SessionRegistry()
    srv = start_mock_server(8796, reg)
    yield reg, srv.base_url
    srv.stop()


@pytest.fixture
def make_client(project, fake_agent, mock, tmp_path):
    reg, base = mock

    def make(responder=None, web_dir=None, agent_kw=None):
        agent = fake_agent(**(agent_kw or {}))
        cfg = project(agent.url)
        (project.root / "scenarios" / "quick-hello.yaml").write_text(GOOD_SCENARIO, encoding="utf-8")
        factory = (lambda: FakeProvider(responder)) if responder else None
        app = create_app(cfg, registry=reg, mock_base_url=base, llm_factory=factory,
                         run_kwargs={"tts": ToneTTS(), "vad": "energy", "stt_fallback": False, "timing": FAST},
                         web_dir=web_dir if web_dir is not None else tmp_path / "no-panel", health_ttl=0)
        return TestClient(app), cfg

    return make


def _wait_suite(client, sid, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = client.get(f"/api/suites/{sid}").json()
        if s["status"] != "running":
            return s
        time.sleep(0.3)
    raise AssertionError("suite did not finish")


def test_health_config_agents(make_client):
    client, cfg = make_client()
    h = client.get("/api/health").json()
    assert set(h) >= {"ok", "version", "checks"}
    assert set(h["checks"]) >= {"tts", "stt", "llm", "agents"}
    assert h["checks"]["agents"] == {"fake": True}
    assert h["checks"]["stt"] is True
    c = client.get("/api/config").json()
    assert c["version"] == 1 and c["agents"][0]["id"] == "fake"
    a = client.get("/api/agents").json()
    assert a[0]["id"] == "fake" and a[0]["reachable"] is True and a[0]["adapter"] == "websocket"


def test_scenarios_crud_and_validate(make_client):
    client, cfg = make_client()
    lst = client.get("/api/scenarios").json()
    row = next(s for s in lst if s["id"] == "quick-hello")
    assert row == {**row, "title": "Quick hello", "agent": "fake", "risk": "normal", "tags": ["smoke"],
                   "language": "en", "mode": "scripted", "file": "scenarios/quick-hello.yaml", "valid": True}
    one = client.get("/api/scenarios/quick-hello").json()
    assert one["id"] == "quick-hello" and "Quick hello" in one["yaml"] and one["parsed"]["agent"] == "fake"
    assert client.get("/api/scenarios/nope").status_code == 404

    bad = client.post("/api/scenarios/validate", json={"yaml": "id: x\ntitle: t\nagent: fake\ncaller: {mode: ai}\n"}).json()
    assert bad["ok"] is False and bad["errors"] and {"path", "message"} <= set(bad["errors"][0])
    assert client.post("/api/scenarios/validate", json={"yaml": GOOD_SCENARIO}).json() == {"ok": True, "errors": []}

    new = GOOD_SCENARIO.replace("quick-hello", "second-one")
    r = client.put("/api/scenarios/second-one", json={"yaml": new}).json()
    assert r["ok"] is True, r
    assert (cfg.scenarios_path / "second-one.yaml").read_text(encoding="utf-8") == new
    mismatch = client.put("/api/scenarios/other-id", json={"yaml": new}).json()
    assert mismatch["ok"] is False and mismatch["errors"][0]["path"] == "id"
    unknown_agent = client.put("/api/scenarios/second-one", json={"yaml": new.replace("agent: fake", "agent: ghost")}).json()
    assert unknown_agent["ok"] is False and unknown_agent["errors"][0]["path"] == "agent"
    steps = {s["id"]: s["done"] for s in client.get("/api/onboarding").json()["steps"]}
    assert steps["scenario_created"] is True
    assert client.delete("/api/scenarios/second-one").json() == {"ok": True}
    assert not (cfg.scenarios_path / "second-one.yaml").exists()
    assert client.delete("/api/scenarios/second-one").status_code == 404
    assert client.get("/api/scenarios/..%5Cx").status_code in (400, 404)


def test_fixtures(make_client):
    client, cfg = make_client()
    lst = client.get("/api/fixtures").json()
    assert lst[0]["id"] == "acme-clinic" and lst[0]["file"] == "fixtures/acme-clinic.json"
    one = client.get("/api/fixtures/acme-clinic").json()
    assert one["id"] == "acme-clinic" and isinstance(one["json"], dict) and one["json"]["tools"]
    fx = dict(one["json"], id="tiny", tools=[], state={})
    assert client.put("/api/fixtures/tiny", json={"json": fx}).json() == {"ok": True, "errors": []}
    assert json.loads((cfg.fixtures_path / "tiny.json").read_text(encoding="utf-8"))["id"] == "tiny"
    assert client.put("/api/fixtures/tiny", json={"json": json.dumps(fx)}).json()["ok"] is True
    bad = client.put("/api/fixtures/tiny", json={"json": {"id": "tiny", "tools": [{"name": "x"}]}}).json()
    assert bad["ok"] is False and bad["errors"]
    assert client.get("/api/fixtures/missing").status_code == 404


def test_suite_run_audio_stats_onboarding(make_client):
    client, cfg = make_client(agent_kw={"replies": ["Hello, how can I help?"]})
    assert client.post("/api/suites", json={"scenario_ids": ["nope"]}).status_code == 400
    r = client.post("/api/suites", json={"scenario_ids": ["quick-hello"]})
    assert r.status_code == 200, r.text
    sid = r.json()["suite_id"]
    busy = client.post("/api/suites", json={"scenario_ids": ["quick-hello"]})
    assert busy.status_code == 409 and "already running" in busy.json()["detail"]
    first = client.get(f"/api/suites/{sid}").json()
    assert first["status"] in ("running", "finished") and first["total"] == 1
    s = _wait_suite(client, sid)
    assert s["status"] == "finished" and s["passed"] == 1 and s["pass_rate"] == 1.0 and s["gate_passed"] is True
    assert len(s["runs"]) == 1 and s["runs"][0]["scenario_id"] == "quick-hello"
    lst = client.get("/api/suites").json()
    assert lst[0]["suite_id"] == sid and lst[0]["status"] == "finished" and "runs" not in lst[0]

    runs = client.get("/api/runs", params={"scenario_id": "quick-hello", "limit": 5}).json()
    assert len(runs) == 1 and "path" not in runs[0]
    rid = runs[0]["run_id"]
    assert set(runs[0]) >= {"run_id", "suite_id", "scenario_id", "status", "started_at", "latency_p50_ms", "latency_p95_ms", "wer"}
    full = client.get(f"/api/runs/{rid}").json()
    assert full["run_id"] == rid and full["status"] == "passed" and full["turns"]
    for name in ("stereo", "caller", "agent"):
        a = client.get(f"/api/runs/{rid}/audio/{name}")
        assert a.status_code == 200 and a.headers["content-type"] == "audio/wav"
        assert a.content[:4] == b"RIFF" and len(a.content) > 1000
    assert client.get(f"/api/runs/{rid}/audio/other").status_code == 404
    assert client.get("/api/runs/r_missing").status_code == 404

    st = client.get("/api/stats").json()
    assert st["suites"] == 1 and st["runs"] == 1 and st["pass_rate_7d"] == 1.0
    assert st["last_suite"]["suite_id"] == sid and st["latency_p50_7d"] == full["metrics"]["latency_p50_ms"]

    steps = {s["id"]: s["done"] for s in client.get("/api/onboarding").json()["steps"]}
    assert steps["first_run"] is True and steps["viewed_report"] is True  # GET /api/runs/{id} marks it
    assert steps["agent_added"] is True  # the test config has agent `fake`, not acme-demo


def test_onboarding_manual_and_ci(make_client):
    client, cfg = make_client()
    body = client.get("/api/onboarding").json()
    assert [s["id"] for s in body["steps"]] == ["demo_running", "first_run", "viewed_report", "agent_added",
                                                 "scenario_created", "ci_enabled"]
    assert all({"id", "title", "done"} == set(s) for s in body["steps"])
    assert client.post("/api/onboarding/bogus").status_code == 404
    wf = cfg.root / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "voice.yml").write_text("jobs:\n  t:\n    steps:\n      - run: uv run pv run --gate --junit r.xml\n", encoding="utf-8")
    steps = {s["id"]: s["done"] for s in client.get("/api/onboarding").json()["steps"]}
    assert steps["ci_enabled"] is True
    assert client.post("/api/onboarding/viewed_report").json() == {"ok": True}
    assert json.loads((cfg.data_path / "onboarding.json").read_text(encoding="utf-8"))["done"]["viewed_report"] is True


def test_panchito_valid_proposal_and_context(make_client):
    seen = []

    def responder(system, messages):
        seen.append(system)
        return json.dumps({"reply": "Here is a scenario.", "proposals": [
            {"kind": "scenario", "id": "impatient-cancel", "summary": "impatient caller cancels",
             "content": GOOD_SCENARIO.replace("quick-hello", "impatient-cancel")}]})

    client, cfg = make_client(responder=responder)
    r = client.post("/api/panchito/chat", json={"messages": [{"role": "user", "content": "Write a scenario"}],
                                                "context": {"page": "/scenarios/", "scenario_id": "quick-hello"}}).json()
    assert r["reply"] == "Here is a scenario."
    p = r["proposals"][0]
    assert p["kind"] == "scenario" and p["id"] == "impatient-cancel" and "errors" not in p
    assert not (cfg.scenarios_path / "impatient-cancel.yaml").exists()  # never saved
    assert "quick-hello" in seen[0] and "find_slot" in seen[0] and "SCENARIO quick-hello" in seen[0]
    assert "caller:" in seen[0] and "ANSWER FORMAT" in seen[0]


def test_panchito_invalid_retry_then_errors(make_client):
    calls = []

    def responder(system, messages):
        calls.append(messages)
        return json.dumps({"reply": "Try this.", "proposals": [
            {"kind": "scenario", "id": "broken", "content": "id: broken\ntitle: b\nagent: fake\ncaller: {mode: ai}\n",
             "summary": "bad"}]})

    client, _ = make_client(responder=responder)
    r = client.post("/api/panchito/chat", json={"messages": [{"role": "user", "content": "go"}]}).json()
    assert len(calls) == 2  # asked once more with the errors
    assert "do not pass validation" in calls[1][-1]["content"]
    errs = r["proposals"][0]["errors"]
    assert errs and {"path", "message"} <= set(errs[0])


def test_panchito_run_context_and_plain_text(make_client, project):
    seen = []
    client, cfg = make_client(responder=lambda s, m: (seen.append(s), "Plain text, no JSON.")[1])
    run_dir = cfg.runs_path / "r_test_1"
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(json.dumps({
        "run_id": "r_test_1", "scenario_id": "quick-hello", "status": "failed", "metrics": {"latency_p50_ms": 900},
        "checks": [{"id": "judge_1", "kind": "judge", "passed": False, "status": "failed",
                    "detail": "booked without reading back", "evidence": "Agent: Done, you are booked."}],
        "turns": [{"index": 0, "caller_text": "Hi", "agent_heard": "hi", "agent_text": "Done", "latency_ms": 900}],
        "tool_calls": [{"t_ms": 10, "name": "book_appointment", "args": {"name": "Sam Rivera"}, "response": {"ok": True}}],
    }), encoding="utf-8")
    r = client.post("/api/panchito/chat", json={"messages": [{"role": "user", "content": "Why did this run fail?"}],
                                                "context": {"page": "/runs/view/", "run_id": "r_test_1"}}).json()
    assert r == {**r, "reply": "Plain text, no JSON.", "proposals": []}
    assert "judge_1" in seen[0] and "you are booked" in seen[0] and "book_appointment" in seen[0]


def test_panchito_timeout_and_cap(project):
    cfg = project()

    def slow(system, messages):
        time.sleep(2)
        return "{}"

    r = panchito.chat(cfg, [{"role": "user", "content": "hi"}], provider_factory=lambda: FakeProvider(slow), timeout_s=0.3)
    assert r["proposals"] == [] and "too long" in r["reply"]
    capped = panchito.chat(cfg, [{"role": "user", "content": "hi"}],
                           provider_factory=lambda: FakeProvider(lambda s, m: "{}", max_calls=0))
    assert "max_calls_per_run" in capped["reply"]


def test_static_panel(make_client, tmp_path):
    web = tmp_path / "out"
    (web / "runs" / "view").mkdir(parents=True)
    (web / "_next").mkdir()
    (web / "index.html").write_text("<p>home</p>", encoding="utf-8")
    (web / "runs" / "view" / "index.html").write_text("<p>run view</p>", encoding="utf-8")
    (web / "404.html").write_text("<p>not here</p>", encoding="utf-8")
    (web / "_next" / "app.js").write_text("console.log(1)", encoding="utf-8")
    client, _ = make_client(web_dir=web)
    assert client.get("/").text == "<p>home</p>"
    assert client.get("/runs/view/?id=r_1").text == "<p>run view</p>"
    assert client.get("/runs/view").text == "<p>run view</p>"
    js = client.get("/_next/app.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    nf = client.get("/no/such/page/")
    assert nf.status_code == 404 and "not here" in nf.text
    assert client.get("/api/nothing").status_code == 404 and client.get("/api/nothing").json()["detail"] == "Not Found"
    assert client.get("/../../etc/passwd").status_code == 404
    cors = client.get("/api/config", headers={"Origin": "http://localhost:3700"})
    assert cors.headers["access-control-allow-origin"] == "http://localhost:3700"


def test_missing_panel_page(make_client):
    client, _ = make_client()
    r = client.get("/")
    assert r.status_code == 200 and "npm run build" in r.text


def test_mock_router_mounted(make_client, mock):
    reg, _ = mock
    client, cfg = make_client()
    from panchovoice.fixture import load_fixture

    s = reg.create(load_fixture(cfg.fixtures_path / "acme-clinic.json"))
    try:
        tools = client.get(f"/mock/{s.session_id}/tools").json()
        assert "find_slot" in [t["name"] for t in tools]
        r = client.post(f"/mock/{s.session_id}/tools/find_slot", json={"day": "Tuesday", "time": "10 am"}).json()
        assert r["ok"] is True
    finally:
        reg.remove(s.session_id)
