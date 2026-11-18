"""LLM judge: one call per run answers every yes/no question with an exact quote as evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .llm import LLMProvider

JUDGE_SYSTEM = (
    "You are a strict QA reviewer of phone calls between a CALLER (a test customer) and an AGENT "
    "(the voice assistant under test). You answer yes/no questions about the AGENT's behavior using ONLY "
    "the transcript and the tool call log. Tool calls are shown at the moment they happened. "
    "For each question set passed=true only when the transcript clearly shows it is true. "
    "Evidence must be an exact quote copied from one transcript line (or a tool call line), "
    "at most 25 words. Never invent text."
)

JUDGE_SHAPE = '{"answers": [{"question": string, "passed": boolean, "evidence": string}]}'


@dataclass
class JudgeAnswer:
    question: str
    passed: bool | None
    evidence: str
    error: str | None = None
    evidence_found: bool = True


def render_transcript(turn_lines: list[dict[str, Any]], tool_calls: list[dict[str, Any]]) -> str:
    """turn_lines: [{t_ms, role: caller|agent, text}] -> timeline text with tool calls merged in."""
    events: list[tuple[int, int, str]] = []
    for ln in turn_lines:
        if not ln.get("text"):
            continue
        who = "Caller" if ln["role"] == "caller" else "Agent"
        events.append((int(ln.get("t_ms") or 0), 0, f"{who}: {ln['text']}"))
    for c in tool_calls:
        args = ", ".join(f"{k}={v!r}" for k, v in (c.get("args") or {}).items())
        events.append((int(c.get("t_ms") or 0), 1, f"[tool call] {c['name']}({args}) -> {c.get('response')}"))
    events.sort(key=lambda e: (e[0], e[1]))
    return "\n".join(f"[{t / 1000:6.1f}s] {txt}" for t, _, txt in events)


def _norm(s: str) -> str:
    return re.sub(r"[^\w]+", " ", (s or "").lower()).strip()


def run_judge(provider: LLMProvider, questions: list[str], transcript: str, language: str = "en") -> list[JudgeAnswer]:
    if not questions:
        return []
    qs = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
    user = (
        f"Call language: {language}.\n\nTRANSCRIPT:\n{transcript or '(empty: the agent said nothing)'}\n\n"
        f"QUESTIONS (answer all {len(questions)}, in order):\n{qs}"
    )
    try:
        data = provider.complete_json(JUDGE_SYSTEM, [{"role": "user", "content": user}], JUDGE_SHAPE)
    except Exception as exc:  # provider down, budget, bad JSON
        return [JudgeAnswer(q, None, "", error=f"judge unavailable: {exc}") for q in questions]
    answers = data.get("answers") if isinstance(data, dict) else data
    if not isinstance(answers, list):
        return [JudgeAnswer(q, None, "", error="judge reply had no answers list") for q in questions]
    norm_tx = _norm(transcript)
    out: list[JudgeAnswer] = []
    for i, q in enumerate(questions):
        a = answers[i] if i < len(answers) and isinstance(answers[i], dict) else None
        if a is None or not isinstance(a.get("passed"), bool):
            out.append(JudgeAnswer(q, None, "", error="judge gave no answer for this question"))
            continue
        ev = str(a.get("evidence") or "").strip().strip('"')
        ev = re.sub(r"^\[\s*\d+(\.\d+)?s\]\s*", "", ev)  # the judge sometimes copies the timeline prefix
        found = bool(ev) and _norm(ev) in norm_tx
        out.append(JudgeAnswer(q, a["passed"], ev, evidence_found=found))
    return out
