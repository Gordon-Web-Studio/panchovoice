"""Claude brain: history conversion, and Brain.respond driven by a stub Claude client (no network)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from acme_agent.brain import BrainError, Timing
from acme_agent.call import Settings
from acme_agent.claude_brain import (CALL_CONNECTED, ClaudeBrain, make_brain, to_claude_messages,
                                     to_claude_tools)
from acme_agent.tools import MockTools

SPECS = [{"name": "find_slot", "description": "Check a slot", "params": {"day": "string", "time": "string"}},
         {"name": "book_appointment", "description": "Book", "params": {"name": "string", "day": "string", "time": "string"}}]


# ----------------------------------------------------------------------------- conversion


def test_tools_convert():
    t = MockTools(None, None)
    t.specs = SPECS
    out = to_claude_tools(t.ollama_tools())
    assert out[0]["name"] == "find_slot"
    assert out[0]["input_schema"]["required"] == ["day", "time"]
    assert "weekday" in out[0]["input_schema"]["properties"]["day"]["description"]


def test_history_converts_to_valid_turns():
    history = [
        {"role": "system", "content": "You are the receptionist."},
        {"role": "assistant", "content": "Hi, thanks for calling."},  # greeting comes first
        {"role": "user", "content": "Tuesday at ten?"},
        {"role": "system", "content": "nudge note"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "toolu_a", "function": {"name": "find_slot", "arguments": {"day": "tuesday", "time": "10:00"}}},
            {"function": {"name": "book_appointment", "arguments": '{"name": "Sam"}'}}]},
        {"role": "tool", "tool_name": "find_slot", "content": '{"ok": true}'},
        {"role": "tool", "tool_name": "book_appointment", "content": '{"ok": false}'},
        {"role": "assistant", "content": ""},  # fully blocked reply: dropped
        {"role": "system", "content": "guard note"},
    ]
    system, msgs = to_claude_messages(history)
    assert system == "You are the receptionist."
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant", "user"]
    assert msgs[0]["content"][0]["text"] == CALL_CONNECTED
    assert [b["text"] for b in msgs[2]["content"]] == ["Tuesday at ten?", "<operator_note>nudge note</operator_note>"]
    uses = msgs[3]["content"]
    assert [b["type"] for b in uses] == ["tool_use", "tool_use"]
    assert uses[0]["id"] == "toolu_a" and uses[1]["input"] == {"name": "Sam"}
    results = msgs[4]["content"]
    assert [r["tool_use_id"] for r in results[:2]] == [uses[0]["id"], uses[1]["id"]]
    assert results[2]["text"] == "<operator_note>guard note</operator_note>"


# ----------------------------------------------------------------------------- respond() with a stub client


class _Stream:
    def __init__(self, texts, calls, stop):
        self._texts, self._calls, self._stop = texts, calls, stop

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    @property
    def text_stream(self):
        async def gen():
            for t in self._texts:
                yield t
        return gen()

    async def get_final_message(self):
        content = [SimpleNamespace(type="tool_use", id=f"toolu_{i}", name=n, input=a) for i, (n, a) in enumerate(self._calls)]
        return SimpleNamespace(stop_reason=self._stop, content=content)


class StubClaude:
    """Plays back one scripted response per request and keeps the requests."""

    def __init__(self, script):
        self.script = list(script)
        self.requests: list[dict] = []
        self.messages = self

    def stream(self, **kw):
        self.requests.append(kw)
        texts, calls = self.script.pop(0)
        return _Stream(texts, calls, "tool_use" if calls else "end_turn")


class FakeTools(MockTools):
    def __init__(self):
        super().__init__(None, None)
        self.specs = SPECS
        self.calls: list[tuple[str, dict]] = []

    async def call(self, name, args):
        self.calls.append((name, args))
        return {"ok": True}


def _run(brain, history, tools):
    async def go():
        return [ev async for ev in brain.respond(history, tools, Timing())]
    return asyncio.run(go())


def test_respond_runs_tools_and_speaks():
    stub = StubClaude([
        (["Let me check. "], [("find_slot", {"day": "tuesday", "time": "10:00"})]),
        (["Tuesday at 10 AM is free. ", "What is your name?"], []),
    ])
    brain = ClaudeBrain("claude-haiku-5-5", client=stub)
    history = [{"role": "system", "content": "sys"}, {"role": "user", "content": "Tuesday at ten?"}]
    tools = FakeTools()
    events = _run(brain, history, tools)

    assert tools.calls == [("find_slot", {"day": "tuesday", "time": "10:00"})]
    said = [e.text for e in events if e.kind == "say"]
    assert said == ["Let me check.", "Tuesday at 10 AM is free.", "What is your name?"]
    first, second = stub.requests
    assert first["model"] == "claude-haiku-5-5" and first["thinking"] == {"type": "disabled"}
    assert first["system"].startswith("sys") and first["tools"][0]["name"] == "find_slot"
    assert "temperature" not in first
    # the second request carries the tool_use and its tool_result with the same id
    use = second["messages"][1]["content"][-1]
    res = second["messages"][2]["content"][0]
    assert use["type"] == "tool_use" and res == {"type": "tool_result", "tool_use_id": use["id"], "content": '{"ok": true}'}


def test_claim_guard_still_works_with_claude():
    stub = StubClaude([
        (["You are booked for Tuesday."], []),  # claim with no tool call: blocked, asked again
        (["Can I have your full name?"], []),
    ])
    events = _run(ClaudeBrain(client=stub), [{"role": "system", "content": "s"}, {"role": "user", "content": "Book me"}], FakeTools())
    assert [e.kind for e in events] == ["guard", "say"]
    assert "operator_note" in str(stub.requests[1]["messages"][-1])


def test_api_errors_become_brain_errors():
    class Failing(StubClaude):
        def stream(self, **kw):
            req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
            raise anthropic.AuthenticationError("bad key", response=httpx.Response(401, request=req), body=None)

    with pytest.raises(BrainError, match="ANTHROPIC_API_KEY"):
        _run(ClaudeBrain(client=Failing([])), [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}], FakeTools())


def test_make_brain_picks_backend():
    assert type(make_brain(None, Settings())).__name__ == "Brain"
    b = make_brain(None, Settings(brain="claude", claude_model="claude-haiku-5-5", bug=False))
    assert isinstance(b, ClaudeBrain) and b.confirm_gate and b.model == "claude-haiku-5-5"
