"""ElevenLabs Agents adapter: unit parts, and end to end runs against a fake ElevenLabs API (real WebSocket)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from panchovoice.adapters.elevenlabs import (decode_agent_audio, encode_caller_audio, parse_format, platform_summary,
                                             platform_tool_calls, ulaw_decode, ulaw_encode)
from panchovoice.audio.pcm import tone
from panchovoice.audio.tts import ToneTTS
from panchovoice.config import AgentConfig, load_config, parse_config_text
from panchovoice.errors import ConfigError
from panchovoice.mocktools import SessionRegistry, _free_port, start_mock_server
from panchovoice.runner import run_scenario
from panchovoice.scenario import validate_scenario_text

FAST = {"end_of_turn_ms": 500, "first_turn_wait_ms": 700, "transcript_end_ms": 300}


# ----------------------------------------------------------------------------- unit


def test_parse_format():
    assert parse_format("pcm_16000") == ("pcm", 16000)
    assert parse_format("pcm_24000") == ("pcm", 24000)
    assert parse_format("ulaw_8000") == ("ulaw", 8000)
    assert parse_format(None) == ("pcm", 16000)
    assert parse_format("opus_48000") == ("pcm", 16000)


def test_ulaw_round_trip_is_close():
    x = tone(200, freq=440, amp=0.5)
    y = ulaw_decode(ulaw_encode(x))
    assert len(y) == len(x)
    err = np.abs(y.astype(np.int32) - x.astype(np.int32))
    assert err.max() < 0.04 * 32768  # mu-law step is at most about 3% of full scale near the peak


def test_audio_conversion_lengths():
    frame = tone(20)  # 320 samples at 16 kHz
    assert len(encode_caller_audio(frame, ("pcm", 16000))) == 640
    assert len(encode_caller_audio(frame, ("ulaw", 8000))) == 160
    assert len(encode_caller_audio(frame, ("pcm", 24000))) == 960
    agent_24k = np.full(2400, 1000, dtype="<i2").tobytes()  # 100 ms at 24 kHz
    assert len(decode_agent_audio(agent_24k, ("pcm", 24000))) == 1600


def test_platform_tool_calls_and_summary():
    record = {"status": "done", "metadata": {"cost_fiat": 0.02, "cost": 150, "termination_reason": "end_call"},
              "analysis": {"call_successful": "success"},
              "transcript": [{"role": "agent", "time_in_call_secs": 3, "tool_calls": [
                  {"request_id": "a", "tool_name": "send_sms", "params_as_json": '{"to": "555"}', "type": "webhook"},
                  {"request_id": "b", "tool_name": "find_slot", "params_as_json": "{}", "type": "client"},
                  {"request_id": "c", "tool_name": "end_call", "params_as_json": "{}", "type": "system"},
              ], "tool_results": [{"request_id": "a", "result_value": '{"ok": true}', "is_error": False}]}]}
    calls, notes = platform_tool_calls(record)
    assert calls == [{"t_ms": 3000, "name": "send_sms", "args": {"to": "555"}, "response": {"ok": True},
                      "source": "elevenlabs:webhook", "is_error": False}]
    assert any("end_call" in n for n in notes) and any("real backend" in n for n in notes)
    s = platform_summary(record, "conv_1")
    assert (s["cost_usd"], s["termination_reason"], s["conversation_id"]) == (0.02, "end_call", "conv_1")


def test_agent_config():
    a = AgentConfig(id="el", adapter="elevenlabs", elevenlabs={"agent_id": "agent_abc"})
    assert a.url == "wss://api.elevenlabs.io/v1/convai/conversation?agent_id=agent_abc"
    with pytest.raises(ValueError, match="needs an 'elevenlabs' section"):
        AgentConfig(id="el", adapter="elevenlabs")
    with pytest.raises(ValueError, match="agent_id"):
        AgentConfig(id="el", adapter="elevenlabs", elevenlabs={"agent_id": "agent abc?x=1"})
    with pytest.raises(ConfigError):
        parse_config_text("version: 1\nagents:\n  - {id: el, adapter: elevenlabs, elevenlabs: {agent_id: a, api_key: x}}\n")


# ----------------------------------------------------------------------------- end to end


@pytest.fixture(scope="module")
def mock():
    reg = SessionRegistry()
    srv = start_mock_server(_free_port(), reg)
    yield reg, srv.base_url
    srv.stop()


@pytest.fixture
def fake_el():
    from fake_elevenlabs import FakeElevenLabs, FakeElevenLabsConfig

    servers = []

    def start(**kw):
        s = FakeElevenLabs(FakeElevenLabsConfig(**kw)).start()
        servers.append(s)
        return s

    yield start
    for s in servers:
        s.stop()


@pytest.fixture
def el_project(project, monkeypatch):
    """Config with one elevenlabs agent pointed at the fake API."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")

    def make(api_url: str, extra_el: str = "") -> object:
        root = project.root
        (root / "panchovoice.yaml").write_text(
            "version: 1\n"
            "data_dir: ./data\nscenarios_dir: ./scenarios\nfixtures_dir: ./fixtures\n"
            "llm: {provider: ollama, model: none, max_calls_per_run: 10}\n"
            "agents:\n"
            "  - id: fake\n    name: Fake ElevenLabs\n    adapter: elevenlabs\n    language: en\n"
            f"    elevenlabs: {{agent_id: agent_test, api_url: '{api_url}', conversation_wait_s: 3{extra_el}}}\n",
            encoding="utf-8",
        )
        return load_config(root / "panchovoice.yaml")

    return make


def _scenario(body: str, sid: str = "el-run"):
    scn, errors = validate_scenario_text(f"id: {sid}\ntitle: el\nagent: fake\nfixture: acme-clinic.json\n" + body)
    assert not errors, errors
    return scn


SCRIPTED = """
caller:
  mode: scripted
  turns:
    - say: "Hi, I need Tuesday at ten."
    - say: "Sam Rivera."
expect:
  tool_calls:
    - {name: find_slot, args: {day: tuesday, time: "10:00"}}
    - {name: book_appointment, args: {name: Sam Rivera}}
    - {name: send_confirmation_sms, args: {name: Sam Rivera}}
  order: [find_slot, book_appointment]
  final_state:
    appointments: [{name: Sam Rivera, day: tuesday, time: "10:00"}]
  wer_max: 0.3
  must_say: ["booked"]
limits: {max_turns: 4, max_seconds: 30, silence_timeout_s: 4}
"""


def test_elevenlabs_scripted_run(el_project, fake_el, mock):
    reg, base = mock
    el = fake_el(
        heard=["hi i need tuesday at 10", "sam rivera"],
        replies=["Tuesday at 10 is free. Your name?", "You are booked, Sam."],
        client_tools={0: ("find_slot", {"day": "Tuesday", "time": "10 am"}),
                      1: ("book_appointment", {"name": "Sam Rivera", "day": "tuesday", "time": "10"})},
        webhook_tools=[{"name": "send_confirmation_sms", "args": {"name": "Sam Rivera"}}],
    )
    cfg = el_project(el.api_url, ", dynamic_variables: {customer: Sam}")
    res = run_scenario(_scenario(SCRIPTED), cfg, tts=ToneTTS(), registry=reg, mock_base_url=base,
                       vad="energy", stt_fallback=False, timing=FAST)
    assert res["status"] == "passed", json.dumps(res["checks"], indent=1) + str(res["error"]) + str(res["notes"])

    # transport: key used for the signed url, client data sent, ping answered, tools answered from the mock
    assert el.signed_url_keys == ["test-key"]
    assert el.client_data[0]["dynamic_variables"] == {"customer": "Sam"}
    assert el.pongs == [{"type": "pong", "event_id": 99}]
    assert [json.loads(r["result"])["ok"] for r in el.tool_results] == [True, True]
    assert el.received_samples > 0

    # tool calls: two from the mock (client tools), one from the conversation record (webhook)
    sources = {c["name"]: c.get("source") for c in res["tool_calls"]}
    assert sources["send_confirmation_sms"] == "elevenlabs:webhook"
    assert sources["book_appointment"] == "mock"
    assert res["platform"]["conversation_id"] == "conv_test_1"
    assert res["platform"]["cost_usd"] == 0.0123
    assert res["artifacts"]["platform"] == "platform.json"
    assert res["metrics"]["wer"] is not None


def test_elevenlabs_ulaw_greeting_and_agent_end(el_project, fake_el, mock):
    reg, base = mock
    el = fake_el(in_format="ulaw_8000", out_format="pcm_24000", greet_ms=600, end_after_turns=1,
                 heard=["hello"], replies=["Goodbye."])
    cfg = el_project(el.api_url)
    body = """
caller:
  mode: scripted
  turns:
    - say: "Hello."
    - say: "Are you there?"
limits: {max_turns: 4, max_seconds: 20, silence_timeout_s: 3}
"""
    res = run_scenario(_scenario(body, "el-end"), cfg, tts=ToneTTS(), registry=reg, mock_base_url=base,
                       vad="energy", stt_fallback=False, timing=FAST)
    assert res["ended_by"] == "agent", res
    assert not any(c["id"] == "call_completed" for c in res["checks"])
    assert res["status"] == "passed", json.dumps(res["checks"], indent=1) + str(res["error"]) + str(res["notes"])


def test_elevenlabs_bad_key_is_an_error_not_a_failure(el_project, fake_el, mock, monkeypatch):
    reg, base = mock
    el = fake_el()
    cfg = el_project(el.api_url)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "wrong")
    res = run_scenario(_scenario('caller: {mode: scripted, turns: [{say: "Hi."}]}\n', "el-key"), cfg, tts=ToneTTS(),
                       registry=reg, mock_base_url=base, vad="energy", stt_fallback=False, timing=FAST)
    assert res["status"] == "error"
    assert "rejected ELEVENLABS_API_KEY" in res["error"]


def test_wizard_writes_an_elevenlabs_agent():
    from panchovoice import wizard

    agents = [dict(wizard.DEMO_AGENT),
              {"id": "my-el", "name": "My EL", "adapter": "elevenlabs", "agent_id": "agent_123", "language": "es"}]
    text = wizard.render_config("claude-cli", "haiku", agents, None, "deepgram", "deepgram")
    cfg = parse_config_text(text)
    a = cfg.agent("my-el")
    assert a is not None and a.adapter == "elevenlabs" and a.elevenlabs.agent_id == "agent_123"
    assert (cfg.tts.provider, cfg.stt.provider) == ("deepgram", "deepgram")


def test_doctor_warns_without_key(monkeypatch):
    from panchovoice import doctor

    cfg = parse_config_text("version: 1\nagents:\n  - {id: el, adapter: elevenlabs, elevenlabs: {agent_id: agent_1}}\n")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    [c] = doctor.check_elevenlabs_agents(cfg)
    assert c.status == "WARN" and "ELEVENLABS_API_KEY" in c.fix
    monkeypatch.setenv("ELEVENLABS_API_KEY", "x")
    assert doctor.check_elevenlabs_agents(cfg)[0].status == "PASS"
