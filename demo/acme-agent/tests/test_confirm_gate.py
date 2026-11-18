"""Confirmation gate: with the bug off, book_appointment runs only after a read back and a yes."""

from __future__ import annotations

import asyncio

import pytest

from acme_agent.brain import Brain, Timing, confirmation_ok, extract_slot, is_affirmative, mentions_slot

ARGS = {"name": "Sam Rivera", "day": "tuesday", "time": "10:00"}


def _text(s: str) -> list[dict]:
    return [{"message": {"content": w + " "}} for w in s.split()] + [{"done": True}]


def _book() -> list[dict]:
    return [{"message": {"content": "", "tool_calls": [{"function": {"name": "book_appointment", "arguments": ARGS}}]}},
            {"done": True}]


class FakeTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def ollama_tools(self):
        return [{"type": "function", "function": {"name": "book_appointment"}}]

    async def call(self, name, args):
        self.calls.append((name, args))
        return {"ok": True}


def history(agent_reply: str, caller: str) -> list[dict]:
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "I need Tuesday at 10 AM, my name is Sam Rivera."},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "find_slot"}}]},
        {"role": "tool", "tool_name": "find_slot", "content": "{\"ok\": true}"},
        {"role": "assistant", "content": agent_reply},
        {"role": "user", "content": caller},
    ]


def run(messages: list[dict], gate: bool, rounds: list[list[dict]]):
    brain = Brain(client=None, url="http://x", model="m", confirm_gate=gate)
    script = iter(rounds)
    seen: list[list[dict]] = []

    async def fake_stream(msgs, tools_):
        seen.append(list(msgs))
        for chunk in next(script):
            yield chunk

    brain._chat_stream = fake_stream
    tools = FakeTools()

    async def go():
        return [e async for e in brain.respond(messages, tools, Timing())]

    events = asyncio.run(go())
    return tools, events, seen


# ---- helpers --------------------------------------------------------------------------


@pytest.mark.parametrize("text,ok", [
    ("Is Tuesday at 10 a.m. correct for you?", True),
    ("So that is Tuesday at 10 AM, right?", True),
    ("Tuesday at ten o'clock, is that right?", True),
    ("Confirmo: el martes a las 10:00, correcto?", True),
    ("Is Tuesday at 11 a.m. correct?", False),
    ("Is Wednesday at 10 a.m. correct?", False),
    ("Is 10 a.m. correct?", False),
])
def test_mentions_slot(text, ok):
    assert mentions_slot(text, "tuesday", "10:00") is ok


@pytest.mark.parametrize("text,ok", [
    ("Yes, that is correct.", True), ("Yeah.", True), ("That is right.", True), ("Sure, go ahead.", True),
    ("Sí, correcto.", True), ("Claro.", True),
    ("No, Wednesday please.", False), ("That's not right.", False), ("My name is Sam Rivera.", False),
    ("Wait, make it 11.", False),
])
def test_is_affirmative(text, ok):
    assert is_affirmative(text) is ok


def test_confirmation_needs_question_same_slot_and_yes():
    assert confirmation_ok(history("Is Tuesday at 10 a.m. correct?", "Yes."), ARGS)
    assert not confirmation_ok(history("Tuesday at 10 a.m. is free.", "Yes."), ARGS)  # not a question
    assert not confirmation_ok(history("Is Tuesday at 11 a.m. correct?", "Yes."), ARGS)  # other time
    assert not confirmation_ok(history("Is Tuesday at 10 a.m. correct?", "Sam Rivera."), ARGS)  # no yes
    assert not confirmation_ok([{"role": "user", "content": "Yes"}], ARGS)  # nothing read back


# ---- bug off: the gate holds the booking -----------------------------------------------


def test_bug_off_blocks_booking_without_read_back():
    msgs = history("Tuesday at 10 a.m. is free. What is your name?", "My name is Sam Rivera.")
    tools, events, seen = run(msgs, gate=True, rounds=[_book(), _text("Just to confirm, Tuesday at 10 AM, is that correct?")])
    assert tools.calls == []  # mock tool never called
    assert [e.kind for e in events][:1] == ["gate"]
    tool_msg = [m for m in seen[1] if m["role"] == "tool"][-1]
    assert "confirm_first" in tool_msg["content"] and "read the day and time back" in tool_msg["content"]
    assert [e.text for e in events if e.kind == "say"] == ["Just to confirm, Tuesday at 10 AM, is that correct?"]


def test_bug_off_allows_booking_after_read_back_and_yes():
    msgs = history("Is Tuesday at 10 a.m. correct for you, Sam?", "Yes, that is correct.")
    tools, events, _ = run(msgs, gate=True, rounds=[_book(), _text("You are booked for Tuesday at 10 AM.")])
    assert tools.calls == [("book_appointment", ARGS)]
    assert "gate" not in [e.kind for e in events]


# ---- nudge after a confirmed yes (bug off only) ----------------------------------------


def _notes(seen_msgs):
    return [m["content"] for m in seen_msgs if m["role"] == "system" and "just confirmed" in m["content"]]


def test_extract_slot():
    assert extract_slot("Is Wednesday at 9 AM correct?") == ("wednesday", "09:00")
    assert extract_slot("So Tuesday at 2, right?") == ("tuesday", "14:00")
    assert extract_slot("El martes a las 10:00, correcto?") == ("tuesday", "10:00")
    assert extract_slot("What is your name?") is None


def test_nudge_added_in_no_bug_mode_after_confirmed_yes():
    msgs = history("Is Wednesday at 9 AM correct?", "Yes, that is right.")
    _, events, seen = run(msgs, gate=True, rounds=[_text("Okay.")])
    notes = _notes(seen[0])
    assert len(notes) == 1
    assert notes[0].startswith("The caller just confirmed wednesday at 09:00.")
    assert "book_appointment" in notes[0] and "Do not ask for confirmation again." in notes[0]
    assert "nudge" in [e.kind for e in events]


@pytest.mark.parametrize("reply,caller", [
    ("Is Wednesday at 9 AM correct?", "No, Thursday please."),  # no yes
    ("Wednesday at 9 AM is free.", "Yes."),  # not a question
    ("What is your name?", "Yes."),  # no read back
])
def test_no_nudge_without_a_confirmed_read_back(reply, caller):
    _, events, seen = run(history(reply, caller), gate=True, rounds=[_text("Okay.")])
    assert _notes(seen[0]) == [] and "nudge" not in [e.kind for e in events]


def test_no_nudge_in_bug_mode():
    _, events, seen = run(history("Is Wednesday at 9 AM correct?", "Yes, that is right."), gate=False,
                          rounds=[_text("Okay.")])
    assert _notes(seen[0]) == [] and "nudge" not in [e.kind for e in events]


def test_duplicate_tool_call_in_one_turn_reuses_first_result():
    dup = [{"message": {"content": "", "tool_calls": [
        {"function": {"name": "book_appointment", "arguments": ARGS}},
        {"function": {"name": "book_appointment", "arguments": {"time": "10:00", "name": "sam rivera", "day": "Tuesday"}}},
    ]}}, {"done": True}]
    msgs = history("Is Tuesday at 10 a.m. correct?", "Yes, that is correct.")
    tools, events, seen = run(msgs, gate=True, rounds=[dup, _text("You are booked for Tuesday at 10 AM.")])
    assert len(tools.calls) == 1  # the mock was called once
    assert [e.kind for e in events].count("dup") == 1
    assert all('"ok": true' in m["content"] for m in seen[1] if m["role"] == "tool" and m["tool_name"] == "book_appointment")


# ---- bug on: unchanged, books right away ------------------------------------------------


def test_bug_on_books_without_confirmation():
    msgs = history("Tuesday at 10 a.m. is free. What is your name?", "My name is Sam Rivera.")
    tools, events, _ = run(msgs, gate=False, rounds=[_book(), _text("You are booked for Tuesday at 10 AM.")])
    assert tools.calls == [("book_appointment", ARGS)]
    assert "gate" not in [e.kind for e in events]


def test_call_wires_gate_to_bug_flag():
    from acme_agent.call import Call, Settings

    class _T:
        pass

    on = Call(None, Settings(bug=True), _T(), None, 1)
    off = Call(None, Settings(bug=False), _T(), None, 2)
    assert on.brain.confirm_gate is False and off.brain.confirm_gate is True
