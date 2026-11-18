"""Claim guard: the agent never says "you are booked" unless book_appointment succeeded."""

from __future__ import annotations

import asyncio

import pytest

from acme_agent.brain import GUARD_FALLBACK, Brain, Timing, claim_kind, strip_placeholders


def _text(s: str) -> list[dict]:
    return [{"message": {"content": w + " "}} for w in s.split()] + [{"done": True}]


def _tool(tool_name: str, **args) -> list[dict]:
    return [{"message": {"content": "", "tool_calls": [{"function": {"name": tool_name, "arguments": args}}]}},
            {"done": True}]


class FakeTools:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[tuple[str, dict]] = []

    def ollama_tools(self):
        return [{"type": "function", "function": {"name": "book_appointment"}}]

    async def call(self, name, args):
        self.calls.append((name, args))
        return {"ok": True} if self.ok else {"ok": False, "error": "slot_not_available"}


def run(rounds: list[list[dict]], tools: FakeTools | None = None, language: str = "en"):
    brain = Brain(client=None, url="http://x", model="m")
    script = iter(rounds)
    seen: list[list[dict]] = []

    async def fake_stream(messages, tools_):
        seen.append(list(messages))
        for chunk in next(script):
            yield chunk

    brain._chat_stream = fake_stream
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "book me"}]
    tools = tools or FakeTools()

    async def go():
        return [e async for e in brain.respond(messages, tools, Timing(), language)]

    events = asyncio.run(go())
    said = [e.text for e in events if e.kind == "say"]
    return said, events, messages, seen, tools


@pytest.mark.parametrize("s,kind", [
    ("You are booked for Tuesday at 10 AM.", "book"),
    ("Your appointment is scheduled.", "book"),
    ("Listo, su cita quedo agendada.", "book"),
    ("Su cita esta reservada para el martes.", "book"),
    ("Your appointment has been cancelled.", "cancel"),
    ("La cita fue cancelada.", "cancel"),
    ("Shall I get that scheduled for you?", None),
    ("Nothing is booked yet.", None),
    ("I have not booked anything.", None),
    ("Tuesday at 10 AM is free.", None),
])
def test_claim_kind(s, kind):
    assert claim_kind(s) == kind


def test_false_claim_is_not_spoken_and_model_is_asked_again():
    said, events, messages, seen, tools = run([
        _text("Great, you are all booked for Tuesday at 10 AM. See you then!"),
        _text("Can I have your full name please?"),
    ])
    assert said == ["Can I have your full name please?"]
    assert not any("booked" in s for s in said)
    assert [e.kind for e in events].count("guard") == 1
    # the second model call saw the guard note, and never the blocked claim
    assert any(m["role"] == "system" and "book_appointment has not succeeded" in m["content"] for m in seen[1])
    assert not any("booked" in (m.get("content") or "") for m in messages if m["role"] == "assistant")


def test_retry_that_calls_the_tool_lets_the_claim_through():
    said, events, messages, seen, tools = run([
        _text("You are booked for Tuesday."),
        _tool("book_appointment", name="Sam Rivera", day="tuesday", time="10:00"),
        _text("You are booked for Tuesday at 10 AM."),
    ])
    assert tools.calls == [("book_appointment", {"name": "Sam Rivera", "day": "tuesday", "time": "10:00"})]
    assert said == ["You are booked for Tuesday at 10 AM."]


def test_claim_after_successful_tool_is_spoken_without_guard():
    said, events, *_ = run([
        _tool("book_appointment", name="Sam Rivera", day="tuesday", time="10:00"),
        _text("Done, you are booked for Tuesday at 10 AM."),
    ])
    assert said == ["Done, you are booked for Tuesday at 10 AM."]
    assert "guard" not in [e.kind for e in events]


def test_failed_tool_does_not_unlock_the_claim():
    said, *_ = run([
        _tool("book_appointment", name="Sam Rivera", day="friday", time="10:00"),
        _text("You are booked for Friday."),
        _text("Sorry, Friday at 10 AM is not available."),
    ], tools=FakeTools(ok=False))
    assert said == ["Sorry, Friday at 10 AM is not available."]


def test_second_false_claim_gets_the_safe_fallback():
    said, events, *_ = run([_text("You are booked."), _text("All set, it is scheduled.")])
    assert said == [GUARD_FALLBACK["en"]]
    assert [e.kind for e in events].count("guard") == 2


def test_sentences_before_the_claim_are_still_spoken():
    said, *_ = run([
        _text("Tuesday at 10 AM is free. You are booked."),
        _text("What is your full name?"),
    ])
    assert said == ["Tuesday at 10 AM is free.", "What is your full name?"]


@pytest.mark.parametrize("raw,clean", [
    ("Hello [Caller's Name], how can I help?", "Hello, how can I help?"),
    ("Thanks, [name].", "Thanks."),
    ("Hi {name} , welcome to ACME clinic.", "Hi, welcome to ACME clinic."),
    ("Tuesday at 10 AM is free.", "Tuesday at 10 AM is free."),
    ("[Caller's Name]", ""),
])
def test_strip_placeholders(raw, clean):
    assert strip_placeholders(raw) == clean


def test_placeholders_never_reach_speech():
    said, *_ = run([_text("Hello [Caller's Name], what day works for you? [name]")])
    assert said == ["Hello, what day works for you?"]


def test_prompt_forbids_placeholders():
    from acme_agent.prompts import system_prompt

    for bug in (True, False):
        assert "Never write placeholders in brackets" in system_prompt(bug)


def test_spanish_fallback():
    said, *_ = run([_text("Su cita quedo agendada."), _text("Listo, quedo reservada.")], language="es")
    assert said == [GUARD_FALLBACK["es"]]
