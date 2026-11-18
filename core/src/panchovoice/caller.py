"""Callers: scripted (turns from the scenario) and AI (an LLM plays the customer)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .llm import LLMProvider
from .scenario import Scenario


@dataclass
class Utterance:
    text: str
    interrupt_after_ms: int | None = None
    pause_ms: int | None = None
    end_call: bool = False  # hang up after saying this


@dataclass
class HistoryItem:
    role: str  # caller | agent
    text: str


class Caller:
    """Base: the adapter asks for the next utterance after each agent turn."""

    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.turns_said = 0

    def next_utterance(self, history: list[HistoryItem]) -> Utterance | None:
        raise NotImplementedError

    def upcoming_texts(self) -> list[str]:
        """Texts known ahead of time (scripted), so TTS can run before the call starts."""
        return []


class ScriptedCaller(Caller):
    def __init__(self, scenario: Scenario):
        super().__init__(scenario)
        self.turns = list(scenario.caller.turns or [])[: scenario.limits.max_turns]

    def upcoming_texts(self) -> list[str]:
        return [t.say for t in self.turns]

    def next_utterance(self, history: list[HistoryItem]) -> Utterance | None:
        if self.turns_said >= len(self.turns):
            return None
        t = self.turns[self.turns_said]
        self.turns_said += 1
        return Utterance(t.say, t.interrupt_after_ms, t.pause_ms, end_call=self.turns_said >= len(self.turns))


AI_CALLER_RULES = """You are role playing a CUSTOMER who is phoning a business. You are NOT the assistant.
Rules:
- You are the customer. Never act as the business, never offer help, never say "How can I help you".
- Say ONE short spoken sentence per turn (at most 20 words). Plain words, no lists, no emojis, no stage directions.
- Use only the facts given below. Never invent names, numbers, dates or details that are not in the facts.
  If the agent asks for something you do not know, say you do not know it.
- Pursue your goal. When the goal is done, or clearly impossible, say a short goodbye and set end_call to true.
- If the agent asks you to confirm something that matches your goal and facts, confirm it.
- Reply with ONE JSON object only: {"say": "...", "end_call": false}"""


class AICaller(Caller):
    def __init__(self, scenario: Scenario, provider: LLMProvider):
        super().__init__(scenario)
        self.provider = provider
        c = scenario.caller
        lang = "Spanish" if scenario.language == "es" else "English"
        traits = c.traits
        self.system = (
            f"{AI_CALLER_RULES}\n\nSpeak {lang}.\n"
            f"PERSONA: {c.persona}\nGOAL: {c.goal}\n"
            f"FACTS (the only facts you know): {json.dumps(c.facts, ensure_ascii=False)}\n"
            f"Patience: about {traits.patience_s:g} seconds of waiting before you get impatient."
        )
        self.max_turns = scenario.limits.max_turns
        self.errors: list[str] = []

    def upcoming_texts(self) -> list[str]:
        return [self.scenario.caller.opening_line] if self.scenario.caller.opening_line else []

    def _messages(self, history: list[HistoryItem]) -> list[dict[str, str]]:
        lines = []
        for h in history:
            who = "AGENT" if h.role == "agent" else "YOU (customer)"
            lines.append(f"{who}: {h.text}")
        convo = "\n".join(lines) if lines else "(the call just connected; the agent has not spoken)"
        return [{"role": "user", "content": f"Conversation so far:\n{convo}\n\nWhat do you (the customer) say next?"}]

    def next_utterance(self, history: list[HistoryItem]) -> Utterance | None:
        if self.turns_said >= self.max_turns:
            return None
        last_turn = self.turns_said + 1 >= self.max_turns
        if self.turns_said == 0 and self.scenario.caller.opening_line:
            self.turns_said += 1
            return Utterance(self.scenario.caller.opening_line, end_call=last_turn)
        try:
            data = self.provider.complete_json(self.system, self._messages(history), '{"say": string, "end_call": boolean}')
        except Exception as exc:
            self.errors.append(str(exc))
            return None
        if not isinstance(data, dict):
            self.errors.append(f"caller reply is not an object: {data!r}")
            return None
        say = str(data.get("say") or "").strip()
        end = bool(data.get("end_call"))
        if not say:
            return None
        self.turns_said += 1
        return Utterance(say, end_call=end or last_turn)


def make_caller(scenario: Scenario, provider: LLMProvider | None) -> Caller:
    if scenario.caller.mode == "ai":
        if provider is None:
            raise ValueError("ai caller needs an LLM provider")
        return AICaller(scenario, provider)
    return ScriptedCaller(scenario)
