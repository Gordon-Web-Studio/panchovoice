from __future__ import annotations

import json
from pathlib import Path

import pytest

from panchovoice.caller import AICaller, HistoryItem, ScriptedCaller
from panchovoice.config import load_config
from panchovoice.errors import ConfigError
from panchovoice.judge import render_transcript, run_judge
from panchovoice.llm import ClaudeCLIProvider, FakeProvider, LLMBudgetError, extract_json
from panchovoice.scenario import validate_scenario_text


def test_extract_json_with_banner():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('PAI banner here\n```json\n{"say": "hi", "end_call": false}\n```\nbye') == {"say": "hi", "end_call": False}
    assert extract_json("text {not json} then {\"ok\": true}") == {"ok": True}
    with pytest.raises(ValueError):
        extract_json("no json at all")


def test_max_calls_cap():
    p = FakeProvider(lambda s, m: "{}", max_calls=2)
    p.complete("s", [{"role": "user", "content": "x"}])
    p.complete("s", [{"role": "user", "content": "x"}])
    with pytest.raises(LLMBudgetError):
        p.complete("s", [{"role": "user", "content": "x"}])
    assert p.usage.calls == 2


def test_claude_envelope_parsing():
    p = ClaudeCLIProvider(exe="claude")
    env = json.dumps([{"type": "system"}, {"type": "result", "is_error": False, "result": '{"x": 1}',
                                            "usage": {"input_tokens": 5, "output_tokens": 7}}])
    assert p._parse_envelope(env) == '{"x": 1}'
    assert p.usage.output_tokens == 7
    assert p._parse_envelope(json.dumps({"type": "result", "result": "ok"})) == "ok"


def test_judge_quotes_and_failures():
    tx = render_transcript(
        [{"t_ms": 1000, "role": "caller", "text": "Book Tuesday at 10."}, {"t_ms": 4000, "role": "agent", "text": "Done, you are booked."}],
        [{"t_ms": 3500, "name": "book_appointment", "args": {"day": "tuesday"}, "response": {"ok": True}}],
    )
    assert tx.index("[tool call] book_appointment") < tx.index("Agent: Done")

    def responder(system, messages):
        assert "QUESTIONS" in messages[0]["content"]
        return 'Sure! {"answers": [{"question": "q1", "passed": false, "evidence": "Agent: Done, you are booked."},' \
               ' {"question": "q2", "passed": true, "evidence": "made up words"}]}'

    a = run_judge(FakeProvider(responder), ["q1", "q2"], tx)
    assert a[0].passed is False and a[0].evidence_found
    assert a[1].passed is True and not a[1].evidence_found
    down = run_judge(FakeProvider(fail=True), ["q1"], tx)
    assert down[0].passed is None and down[0].error


def test_scripted_and_ai_caller():
    text = """
id: x
title: t
agent: a
caller:
  mode: ai
  persona: Sam
  goal: book
  facts: {name: Sam}
  opening_line: Hi there.
limits: {max_turns: 3}
"""
    scn, errors = validate_scenario_text(text)
    assert not errors
    replies = iter(['{"say": "Tuesday please.", "end_call": false}', 'ok {"say": "Bye.", "end_call": true}'])
    p = FakeProvider(lambda s, m: next(replies))
    c = AICaller(scn, p)
    assert c.next_utterance([]).text == "Hi there."
    u = c.next_utterance([HistoryItem("agent", "When?")])
    assert u.text == "Tuesday please." and not u.end_call
    assert "You are NOT the assistant" in p.prompts[0][0]
    u = c.next_utterance([HistoryItem("agent", "Booked.")])
    assert u.end_call
    assert c.next_utterance([]) is None  # max_turns reached

    s2, _ = validate_scenario_text(text.replace("mode: ai", "mode: scripted\n  turns: [{say: one}, {say: two}]"))
    sc = ScriptedCaller(s2)
    assert [sc.next_utterance([]).text, sc.next_utterance([]).end_call, sc.next_utterance([])] == ["one", True, None]


def test_config_defaults_and_errors(tmp_path: Path):
    cfg = load_config(start=tmp_path)
    assert cfg.llm.provider == "claude-cli" and cfg.agent("acme-demo") is not None
    assert cfg.data_path == (tmp_path / "data").resolve()
    (tmp_path / "panchovoice.yaml").write_text("version: 1\nllm: {provider: claude-cli, colour: red}\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_config(start=tmp_path)
    assert exc.value.errors[0].path == "llm.colour"


@pytest.mark.live
def test_claude_cli_real_call():
    p = ClaudeCLIProvider("haiku")
    out = p.complete_json("You answer in JSON.", [{"role": "user", "content": "Say hello."}], '{"say": string}')
    assert isinstance(out, dict) and out.get("say")
