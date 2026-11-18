"""http-turn adapter: config, helpers, and the runner end to end against the fake HTTP agent."""

from __future__ import annotations

import json

import numpy as np
import pytest

from fake_http_agent import FakeHttpAgent, FakeHttpAgentConfig
from panchovoice.adapters.http_turn import (HttpCallRecording, RateLimiter, decode_audio, get_path, map_actions)
from panchovoice.audio.pcm import read_wav
from panchovoice.audio.tts import ToneTTS
from panchovoice.config import load_config, parse_config_text
from panchovoice.errors import ConfigError
from panchovoice.mocktools import SessionRegistry, _free_port, start_mock_server
from panchovoice.runner import run_scenario
from panchovoice.scenario import validate_scenario_text

FULL_HTTP = """
    http:
      base_url: {url}
      auth: {auth}
      transcribe: {{path: /transcribe, field: audio, format: wav, response_text: transcript{tmode}}}
      chat: {{path: /chat, body: messages, response_text: reply, response_actions: actions}}
      speak: {{path: /speak, body_text: text, response: audio}}
      {tools}
      extra_body: {{lang: en, page: /booking}}
"""

TEXT_HTTP = """
    http:
      base_url: {url}
      chat: {{path: /chat}}
"""


@pytest.fixture(scope="module")
def mock():
    reg = SessionRegistry()
    srv = start_mock_server(_free_port(), reg)
    yield reg, srv.base_url
    srv.stop()


@pytest.fixture
def http_agent():
    agents = []

    def start(**kw) -> FakeHttpAgent:
        a = FakeHttpAgent(FakeHttpAgentConfig(**kw)).start()
        agents.append(a)
        return a

    yield start
    for a in agents:
        a.stop()


def _config(project, http_block: str):
    project()  # writes fixtures and folders
    root = project.root
    (root / "panchovoice.yaml").write_text(
        "version: 1\ndata_dir: ./data\nscenarios_dir: ./scenarios\nfixtures_dir: ./fixtures\n"
        "llm: {provider: ollama, model: none, max_calls_per_run: 10}\n"
        "agents:\n"
        "  - id: my-assistant\n    name: My assistant\n    adapter: http-turn\n    language: en\n"
        + http_block,
        encoding="utf-8")
    return load_config(root / "panchovoice.yaml")


def _full(url: str, auth: str = "{type: none}", tools: str = "tools_from: actions", tmode: str = "") -> str:
    return FULL_HTTP.format(url=url, auth=auth, tools=tools, tmode=tmode)


def _scenario(body: str, sid: str = "http-run"):
    scn, errors = validate_scenario_text(f"id: {sid}\ntitle: http\nagent: my-assistant\nfixture: acme-clinic.json\n" + body)
    assert not errors, errors
    return scn


def _run(scn, cfg, mock, **kw):
    reg, base = mock
    return run_scenario(scn, cfg, tts=ToneTTS(), registry=reg, mock_base_url=base, vad="energy",
                        stt_fallback=False, **kw)


BOOKING = """
risk: critical
caller:
  mode: scripted
  turns:
    - say: "Hi, I need Tuesday at ten."
    - say: "Sam Rivera."
expect:
  tool_calls:
    - {name: find_slot, args: {day: tuesday, time: "10:00"}}
    - {name: book_appointment, args: {name: Sam Rivera, day: tuesday}}
  order: [find_slot, book_appointment]
  latency_ms: {p50: 6000, p95: 8000}  # loose: the exact latency contract is asserted per turn below
  wer_max: 0.3
  must_say: ["booked"]
  never_say: ["system prompt"]
limits: {max_turns: 4, max_seconds: 30, silence_timeout_s: 5}
"""


# --------------------------------------------------------------------------- config


def _agent_yaml(body: str) -> str:
    return "agents:\n  - id: a\n    adapter: http-turn\n" + body


def test_config_http_turn_schema():
    cfg = parse_config_text(_agent_yaml("    http: {base_url: 'http://127.0.0.1:9000', chat: {path: /chat}}\n"))
    a = cfg.agents[0]
    assert a.url == "http://127.0.0.1:9000"  # defaults to base_url, so health probes work
    assert a.http.transcribe is None and a.http.speak is None and a.http.chat.body == "messages"
    with pytest.raises(ConfigError):
        parse_config_text(_agent_yaml(""))  # http section missing
    with pytest.raises(ConfigError):
        parse_config_text(_agent_yaml("    http: {base_url: x, chat: {}, auth: {type: bearer}}\n"))  # no env name
    with pytest.raises(ConfigError):
        parse_config_text(_agent_yaml("    http: {base_url: x, chat: {}, auth: {type: bearer, env: 'sk-abc 123'}}\n"))
    with pytest.raises(ConfigError):
        parse_config_text(_agent_yaml("    http: {base_url: x, chat: {}, auth: {type: bearer, token: abc}}\n"))
    with pytest.raises(ConfigError):
        parse_config_text(_agent_yaml("    http: {base_url: x, chat: {}, auth: {type: header, env: MY_KEY}}\n"))
    with pytest.raises(ConfigError):  # websocket still needs a url
        parse_config_text("agents:\n  - {id: w, adapter: websocket}\n")


# --------------------------------------------------------------------------- helpers


def test_map_actions_and_paths():
    acts = [{"type": "book_appointment", "args": {"day": "tuesday"}},
            {"name": "find_slot", "params": {"time": "10:00"}},
            {"name": "notify", "arguments": '{"to": "front desk"}'},
            {"args": {"x": 1}}, "junk"]
    assert map_actions(acts) == [{"name": "book_appointment", "args": {"day": "tuesday"}},
                                 {"name": "find_slot", "args": {"time": "10:00"}},
                                 {"name": "notify", "args": {"to": "front desk"}}]
    assert map_actions(None) == []
    assert get_path({"data": {"choices": [{"text": "hi"}]}}, "data.choices.0.text") == "hi"
    assert get_path({"a": 1}, "b.c") is None


def test_rate_limiter_sleeps_when_window_is_full():
    now = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    rl = RateLimiter(2, clock=lambda: now[0], sleep=sleep)
    rl.wait()
    now[0] = 10
    rl.wait()
    now[0] = 20
    rl.wait()  # third turn inside 60 s: waits until the first one leaves the window
    assert slept == [40.0] and now[0] == 60
    now[0] = 200
    rl.wait()
    assert slept == [40.0]
    assert RateLimiter(None).wait() == 0.0


def test_decode_mp3_and_wav():
    from fake_http_agent import MP3_TONE, _tone_wav

    mp3 = decode_audio(MP3_TONE.read_bytes(), "audio/mpeg")
    assert 0.7 * 16000 < len(mp3) < 0.95 * 16000 and np.abs(mp3.astype(int)).max() > 1000
    wav = decode_audio(_tone_wav(500, sr=24000))
    assert len(wav) == 8000


# --------------------------------------------------------------------------- end to end


def test_http_turn_end_to_end(project, http_agent, mock):
    agent = http_agent(heard=["hi i need tuesday at 10", "Sam Rivera"], stt_delay_ms=100, llm_delay_ms=200,
                       tts_delay_ms=100)
    cfg = _config(project, _full(agent.url))
    res = _run(_scenario(BOOKING), cfg, mock)
    assert res["status"] == "passed", json.dumps(res["checks"], indent=1) + str(res["error"]) + str(res["notes"])
    turns = res["turns"]
    assert len(turns) == 2
    for t in turns:
        assert t["stt_ms"] >= 90 and t["llm_ms"] >= 190 and t["tts_ms"] >= 90
        assert t["pipeline_ms"] >= t["stt_ms"] + t["llm_ms"] + t["tts_ms"] - 5
        # latency is measured on the recording: agent audio starts at caller_end + pipeline time
        assert abs(t["latency_ms"] - t["pipeline_ms"]) <= 30, t
        assert t["agent_start_ms"] - t["caller_end_ms"] == t["latency_ms"]
    assert turns[0]["agent_heard"] == "hi i need tuesday at 10"
    assert turns[1]["agent_text"].startswith("You are booked, Sam")
    assert res["metrics"]["wer"] == 0.0
    names = [c["name"] for c in res["tool_calls"]]
    assert names == ["find_slot", "book_appointment"]
    assert all(c["source"] == "actions" for c in res["tool_calls"])
    assert turns[0]["caller_end_ms"] < res["tool_calls"][0]["t_ms"] < turns[0]["agent_start_ms"]

    # requests: history, extra body, session headers
    chats = [r for r in agent.requests if r["path"] == "/chat"]
    assert chats[1]["body"]["messages"] == [
        {"role": "user", "content": "hi i need tuesday at 10"},
        {"role": "assistant", "content": turns[0]["agent_text"]},
        {"role": "user", "content": "Sam Rivera"},
    ]
    assert chats[0]["body"]["lang"] == "en" and chats[0]["body"]["page"] == "/booking"
    h = chats[0]["headers"]
    assert h["x-pv-session"] and h["x-pv-mock-tools-url"].endswith(h["x-pv-session"])
    assert agent.requests[0]["path"] == "/transcribe" and agent.requests[0]["body"]["audio_bytes"] > 1000

    # stereo recording on one timeline: agent silent while the caller talks, loud after the latency
    run_dir = cfg.runs_path / res["run_id"]
    stereo, sr = read_wav(run_dir / "stereo.wav")
    assert sr == 16000 and stereo.shape[1] == 2
    caller, agent_ch = stereo[:, 0].astype(int), stereo[:, 1].astype(int)
    c0, c1 = turns[0]["caller_start_ms"] * 16, turns[0]["caller_end_ms"] * 16
    assert np.abs(caller[c0:c1]).max() > 1000 and np.abs(agent_ch[c0:c1]).max() == 0
    a0 = turns[0]["agent_start_ms"] * 16
    assert np.abs(agent_ch[a0:a0 + 1600]).max() > 1000
    assert np.abs(caller[a0:a0 + 1600]).max() == 0
    tx = json.loads((run_dir / "transcript.json").read_text(encoding="utf-8"))
    assert [l["role"] for l in tx["lines"]] == ["caller", "agent", "caller", "agent"]


def test_http_turn_mock_tools_and_final_state(project, http_agent, mock):
    """The agent calls the mock tool server itself (headers); no actions mapping."""
    agent = http_agent(heard=["tuesday at 10 please", "my name is Sam Rivera"], use_mock=True, speak_format="mp3")
    cfg = _config(project, _full(agent.url, tools=""))
    body = """
caller: {mode: scripted, turns: [{say: "Tuesday at ten please."}, {say: "My name is Sam Rivera."}]}
expect:
  tool_calls: [{name: book_appointment, args: {name: Sam Rivera, day: tuesday, time: "10:00"}}]
  final_state:
    appointments: [{name: Sam Rivera, day: tuesday, time: "10:00"}]
limits: {max_turns: 3, max_seconds: 30, silence_timeout_s: 5}
"""
    res = _run(_scenario(body), cfg, mock)
    assert res["status"] == "passed", json.dumps(res["checks"], indent=1) + str(res["notes"])
    assert [c["source"] for c in res["tool_calls"]] == ["mock", "mock"]
    t1 = res["turns"][1]
    book = res["tool_calls"][1]
    # wall clock times of the mock log are moved onto the timeline of that turn
    assert t1["caller_end_ms"] <= book["t_ms"] <= t1["agent_start_ms"], (book, t1)
    # mp3 reply decoded onto the agent channel
    assert t1["agent_end_ms"] - t1["agent_start_ms"] >= 600


def test_http_turn_text_mode(project, http_agent, mock):
    """No transcribe and no speak: text latency, silent agent channel, WER is an error, not a failure."""
    agent = http_agent(llm_delay_ms=150)
    cfg = _config(project, TEXT_HTTP.format(url=agent.url))
    body = """
caller: {mode: scripted, turns: [{say: "Hi, I need Tuesday at ten."}, {say: "Sam Rivera."}]}
expect:
  tool_calls: [{name: book_appointment}]
  wer_max: 0.2
  must_say: ["booked"]
limits: {max_turns: 3, max_seconds: 30, silence_timeout_s: 5}
"""
    res = _run(_scenario(body), cfg, mock)
    by = {c["id"]: c for c in res["checks"]}
    assert by["agent_replied"]["status"] == "passed"
    assert by["must_say_1"]["status"] == "passed"
    assert by["wer"]["status"] == "error" and "text mode" in by["wer"]["detail"]
    # actions are not mapped without tools_from: tool_calls fails (the agent never used the mock)
    assert by["tool_calls"]["status"] == "failed"
    assert res["metrics"]["wer"] is None
    for t in res["turns"]:
        assert t["agent_heard"] == "" and t["stt_ms"] is None and t["tts_ms"] is None
        assert t["latency_ms"] is not None and t["latency_ms"] >= 140
    chats = [r for r in agent.requests if r["path"] == "/chat"]
    assert chats[0]["body"]["messages"] == [{"role": "user", "content": "Hi, I need Tuesday at ten."}]
    agent_ch, _ = read_wav(cfg.runs_path / res["run_id"] / "agent.wav")
    assert np.abs(agent_ch.astype(int)).max() == 0


def test_http_turn_barge_in_not_supported(project, http_agent, mock):
    agent = http_agent()
    cfg = _config(project, _full(agent.url))
    body = """
caller:
  mode: scripted
  turns:
    - say: "Tell me all the free slots."
    - say: "Stop, just Tuesday."
      interrupt_after_ms: 800
expect:
  barge_in: {max_stop_ms: 1000}
limits: {max_turns: 3, max_seconds: 30, silence_timeout_s: 5}
"""
    res = _run(_scenario(body), cfg, mock)
    by = {c["id"]: c for c in res["checks"]}
    assert by["barge_in"]["status"] == "error" and by["barge_in"]["detail"] == "not supported by http-turn"
    assert res["status"] == "error"
    assert all(t["interrupted"] is False for t in res["turns"])
    assert any("barge-in is not supported" in n for n in res["notes"])


def test_http_turn_auth_from_env(project, http_agent, mock, monkeypatch):
    agent = http_agent(require_header=("authorization", "Bearer s3cret-test"))
    cfg = _config(project, _full(agent.url, auth="{type: bearer, env: MY_ASSISTANT_TOKEN}"))
    scn = _scenario('caller: {mode: scripted, turns: [{say: "Hi."}]}\nlimits: {max_turns: 2, max_seconds: 20, silence_timeout_s: 5}\n')
    monkeypatch.delenv("MY_ASSISTANT_TOKEN", raising=False)
    res = _run(scn, cfg, mock)
    assert res["status"] == "error" and "MY_ASSISTANT_TOKEN" in res["error"]
    monkeypatch.setenv("MY_ASSISTANT_TOKEN", "wrong")
    res = _run(scn, cfg, mock)
    assert res["status"] == "error" and "401" in res["error"] and "wrong" not in res["error"]
    monkeypatch.setenv("MY_ASSISTANT_TOKEN", "s3cret-test")
    res = _run(scn, cfg, mock)
    assert res["status"] == "passed", res
    assert "s3cret-test" not in json.dumps(res)


def test_http_turn_cookie_and_json_transcribe(project, http_agent, mock, monkeypatch):
    agent = http_agent(require_header=("cookie", "session=abc123"), heard=["hi"])
    cfg = _config(project, _full(agent.url, auth="{type: cookie, env: MY_ASSISTANT_COOKIE}", tmode=", mode: json"))
    monkeypatch.setenv("MY_ASSISTANT_COOKIE", "session=abc123")
    res = _run(_scenario('caller: {mode: scripted, turns: [{say: "Hi."}]}\nlimits: {max_turns: 2, max_seconds: 20, silence_timeout_s: 5}\n'),
               cfg, mock)
    assert res["status"] == "passed", res
    assert agent.requests[0]["body"]["json_keys"] == ["audio"]
    assert res["turns"][0]["agent_heard"] == "hi"


def test_http_turn_failed_turn_and_agent_down(project, http_agent, mock):
    agent = http_agent(fail_chat_turns={0})
    cfg = _config(project, _full(agent.url))
    res = _run(_scenario('caller: {mode: scripted, turns: [{say: "Hi."}, {say: "Tuesday at ten."}]}\n'
                         'limits: {max_turns: 3, max_seconds: 20, silence_timeout_s: 5}\n'), cfg, mock)
    assert res["turns"][0]["agent_start_ms"] is None and res["turns"][1]["agent_start_ms"] is not None
    assert any("answered 500" in n for n in res["notes"])
    cfg = _config(project, _full("http://127.0.0.1:9"))
    res = _run(_scenario('caller: {mode: scripted, turns: [{say: "Hi."}]}\n'), cfg, mock)
    assert res["status"] == "error" and "cannot connect" in res["error"]


def test_recording_timeline_mapping():
    rec = HttpCallRecording(np.zeros(0, np.int16), np.zeros(0, np.int16), [], [], 0.0,
                            windows=[(100, 400, 2000), (900, 1200, 5000)])
    assert rec.timeline_ms(50) == 0
    assert rec.timeline_ms(150) == 2050
    assert rec.timeline_ms(600) == 2300  # between turns: clamp to the end of the last window
    assert rec.timeline_ms(1000) == 5100
