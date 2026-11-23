"""Brain: Ollama /api/chat with tool calling, streamed sentence by sentence."""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx

from .tools import MockTools

MAX_TOOL_ROUNDS = 4


class BrainError(Exception):
    """The LLM could not answer (bad key, network, rate limit). The call speaks an apology."""

_NO_SPLIT = ("a.m.", "p.m.", "dr.", "mr.", "ms.", "mrs.", "st.", "e.g.", "i.e.")
_BOUNDARY = re.compile(r"[.!?](?=\s)")


# ---- claim guard: never say "you are booked" without a successful tool call ------------------

_CLAIM_TOOLS = {"book_appointment": "book", "cancel_appointment": "cancel"}
_CLAIM_WORDS = {
    "book": re.compile(r"\b(booked|scheduled|reserved|reservad[oa]s?|agendad[oa]s?)\b", re.I),
    "cancel": re.compile(r"\b(cancell?ed|cancelad[oa]s?|anulad[oa]s?)\b", re.I),
}
_NEGATION = re.compile(r"n't\b|\b(not|no|never|yet|cannot|unable|todav[ií]a|a[uú]n)\b", re.I)

GUARD_NOTE = {
    "book": "Check: you were about to say the appointment is booked, but book_appointment has not "
            "succeeded in this turn. Do not say it is booked. If you have the name, day and time and "
            "the booking rules allow it, call book_appointment now. Otherwise ask the caller for what is missing.",
    "cancel": "Check: you were about to say the appointment is cancelled, but cancel_appointment has not "
              "succeeded in this turn. Do not say it is cancelled. If you have the name and the day, call "
              "cancel_appointment now. Otherwise ask the caller for what is missing.",
}
GUARD_FALLBACK = {
    "en": "Sorry, nothing is booked or changed yet. Can you tell me the day and time again?",
    "es": "Perdon, todavia no hay nada reservado ni cambiado. Me repite el dia y la hora?",
}


_PLACEHOLDER = re.compile(r"\[[^\[\]]*\]|\{[^{}]*\}")


def strip_placeholders(text: str) -> str:
    """Drop template placeholders like [Caller's Name], [name], {name} so they are never spoken."""
    out = _PLACEHOLDER.sub("", text)
    out = re.sub(r"\s+([,.!?;:])", r"\1", out)  # "Hi , Sam" -> "Hi, Sam"
    out = re.sub(r"([,;:])(?=[,.!?;:])", "", out)  # "Thanks, ." -> "Thanks."
    return re.sub(r"\s{2,}", " ", out).strip()


def claim_kind(sentence: str) -> str | None:
    """'book' or 'cancel' if the sentence states that the action happened, else None.

    Questions ("Shall I get that scheduled?") and negations ("nothing is booked yet") are not claims.
    """
    s = sentence.strip()
    if not s or s.endswith("?") or _NEGATION.search(s):
        return None
    for kind, rx in _CLAIM_WORDS.items():
        if rx.search(s):
            return kind
    return None


class SentenceSplitter:
    """Feed streamed text; get back complete sentences (3 words or more, short ones carry over)."""

    def __init__(self, min_words: int = 3) -> None:
        self.buf = ""
        self.min_words = min_words

    def feed(self, text: str) -> list[str]:
        self.buf += text
        out = []
        start = 0
        for m in _BOUNDARY.finditer(self.buf):
            end = m.end()
            piece = self.buf[start:end].strip()
            last = piece.split()[-1].lower() if piece.split() else ""
            if len(piece.split()) < self.min_words:
                continue
            if last in _NO_SPLIT:
                # "10 a.m. Could you..." ends a sentence; "10 a.m. on Tuesday" does not.
                nxt = self.buf[end:].lstrip()
                if not nxt:
                    break  # wait for more text
                if not (last in ("a.m.", "p.m.") and nxt[0].isupper()):
                    continue
            out.append(piece)
            start = end
        self.buf = self.buf[start:]
        return out

    def flush(self) -> list[str]:
        rest = self.buf.strip()
        self.buf = ""
        return [rest] if rest else []


@dataclass
class Event:
    kind: str  # "say" | "tool"
    text: str = ""
    name: str = ""
    args: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)


@dataclass
class Timing:
    first_sentence_ms: int | None = None
    total_ms: int = 0
    rounds: int = 0


# ---- confirmation gate (bug off only): book only after a read back and a yes -----------------

_DAYS = {
    "monday": "monday", "tuesday": "tuesday", "wednesday": "wednesday", "thursday": "thursday",
    "friday": "friday", "saturday": "saturday", "sunday": "sunday",
    "lunes": "monday", "martes": "tuesday", "miercoles": "wednesday", "miércoles": "wednesday",
    "jueves": "thursday", "viernes": "friday", "sabado": "saturday", "sábado": "saturday", "domingo": "sunday",
}
_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
              "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "noon": 12,
              "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
              "nueve": 9, "diez": 10, "once": 11, "doce": 12}
_TIME_RX = re.compile(
    r"\b(\d{1,2}|" + "|".join(_NUM_WORDS) + r")(?::(\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?|o'clock)?(?![\d:])", re.I
)
_YES = re.compile(
    r"\b(yes|yeah|yep|yup|correct|right|sure|ok|okay|confirm(ed)?|perfect|exactly|absolutely|"
    r"definitely|please do|go ahead|s[ií]|correcto|claro|exacto|vale|perfecto|de acuerdo|eso es|adelante)\b",
    re.I,
)
_NO = re.compile(r"n't\b|\b(no|not|nope|wrong|incorrect|wait|change|different|instead|mejor|otro|otra)\b", re.I)

CONFIRM_FIRST = {
    "ok": False,
    "error": "confirm_first",
    "say": "read the day and time back and ask the caller to confirm",
}


def normalize_day(value: str) -> str:
    v = str(value).strip().lower()
    if v in _DAYS:
        return _DAYS[v]
    for word, day in _DAYS.items():
        if v.startswith(word[:3]):
            return day
    return v


def parse_hm(value: str) -> tuple[int, int] | None:
    """'10:00' / '10' / '10 am' / '2 pm' -> (hour, minute) in 24 hour time."""
    m = re.match(r"^\s*(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?\s*$", str(value), re.I)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if ap.startswith("p") and h < 12:
        h += 12
    if ap.startswith("a") and h == 12:
        h = 0
    return h, mi


def mentions_slot(text: str, day: str, time_: str) -> bool:
    """True if text names the same weekday and the same time (spoken forms like '10 a.m.' count)."""
    low = text.lower()
    want_day = normalize_day(day)
    if not any(re.search(rf"\b{w}\b", low) for w, d in _DAYS.items() if d == want_day):
        return False
    want = parse_hm(time_)
    if want is None:
        return False
    for m in _TIME_RX.finditer(low):
        tok = m.group(1)
        h = int(tok) if tok.isdigit() else _NUM_WORDS[tok]
        mi = int(m.group(2) or 0)
        suffix = (m.group(3) or "").replace(" ", "")
        if suffix.startswith("p") and h < 12:
            h += 12
        elif suffix.startswith("a") and h == 12:
            h = 0
        candidates = {h} if suffix.startswith(("a", "p")) else {h % 24, (h + 12) % 24}
        if want[0] in candidates and want[1] == mi:
            return True
    return False


def is_affirmative(text: str) -> bool:
    return bool(_YES.search(text)) and not _NO.search(text)


def extract_slot(text: str) -> tuple[str, str] | None:
    """First weekday and first time named in text, as ('tuesday', '10:00'). Times with no am/pm
    from 1 to 6 count as afternoon (clinic hours)."""
    low = text.lower()
    day = next((d for w, d in sorted(_DAYS.items(), key=lambda x: low.find(x[0]) if x[0] in low else 10**6)
                if re.search(rf"\b{w}\b", low)), None)
    m = _TIME_RX.search(low)
    if not day or not m:
        return None
    tok = m.group(1)
    h = int(tok) if tok.isdigit() else _NUM_WORDS[tok]
    mi = int(m.group(2) or 0)
    suffix = (m.group(3) or "").replace(" ", "")
    if suffix.startswith("p") and h < 12:
        h += 12
    elif suffix.startswith("a") and h == 12:
        h = 0
    elif not suffix.startswith(("a", "p")) and 1 <= h <= 6:
        h += 12
    if h > 23 or mi > 59:
        return None
    return day, f"{h:02d}:{mi:02d}"


def _last_turn(messages: list[dict]) -> tuple[str, str] | None:
    """(agent's previous spoken reply, caller's latest words)."""
    users = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if not users:
        return None
    last = users[-1]
    prev = users[-2] if len(users) > 1 else 0
    reply = " ".join(
        m.get("content") or "" for m in messages[prev + 1 : last] if m.get("role") == "assistant"
    ).strip()
    return reply, messages[last].get("content") or ""


def confirmed_slot(messages: list[dict]) -> tuple[str, str] | None:
    """The slot the caller just said yes to, if the previous reply was a read back question."""
    t = _last_turn(messages)
    if not t or "?" not in t[0] or not is_affirmative(t[1]):
        return None
    slot = extract_slot(t[0])
    if slot and mentions_slot(t[0], *slot):
        return slot
    return None


def confirmed_note(day: str, time_: str) -> str:
    # Wording chosen by an A/B run against qwen2.5:7b: "then confirm the booking in one sentence"
    # made the model write "you are booked" as text instead of calling the tool (11 of 12 vs 12 of 12).
    return (f"The caller just confirmed {day} at {time_}. If you know the caller's name, your next step is a "
            f"book_appointment tool call (day {day}, time {time_}); reply with no text until the tool result "
            "is back. If you do not know the name, ask for it. Do not ask for confirmation again.")


def confirmation_ok(messages: list[dict], args: dict) -> bool:
    """(a) the agent's previous spoken reply asked a question naming the same day and time, and
    (b) the caller's latest utterance is a yes."""
    users = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if not users:
        return False
    last = users[-1]
    prev = users[-2] if len(users) > 1 else 0
    reply = " ".join(
        m.get("content") or "" for m in messages[prev + 1 : last] if m.get("role") == "assistant"
    ).strip()
    if "?" not in reply or not mentions_slot(reply, args.get("day", ""), args.get("time", "")):
        return False
    return is_affirmative(messages[last].get("content") or "")


def _call_key(name: str, args: dict) -> str:
    norm = {k: (str(v).strip().lower() if isinstance(v, str) else v) for k, v in (args or {}).items()}
    return name + json.dumps(norm, sort_keys=True)


class Brain:
    def __init__(self, client: httpx.AsyncClient, url: str, model: str, confirm_gate: bool = False) -> None:
        self.client = client
        self.url = url.rstrip("/")
        self.model = model
        self.confirm_gate = confirm_gate  # True in no-bug mode: enforce read back + yes in code

    async def warm_up(self) -> None:
        """Load the model into memory so the first call turn is fast."""
        await self.client.post(
            f"{self.url}/api/chat",
            json={"model": self.model, "messages": [], "keep_alive": "30m"},
            timeout=180,
        )

    async def _chat_stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[dict]:
        body = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "keep_alive": "30m",
            "options": {"temperature": 0.2, "num_predict": 160},
        }
        if tools:
            body["tools"] = tools
        async with self.client.stream("POST", f"{self.url}/api/chat", json=body, timeout=120) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.strip():
                    yield json.loads(line)

    async def respond(
        self, messages: list[dict], tools: MockTools, timing: Timing, language: str = "en"
    ) -> AsyncIterator[Event]:
        """Run the model with tools until it answers in plain text. Appends to `messages`.

        Claim guard: a sentence that says a booking or cancellation happened is never spoken
        unless the matching tool succeeded in this turn. The model is asked again once.
        """
        t0 = time.perf_counter()
        ollama_tools = tools.ollama_tools()
        if self.confirm_gate and (slot := confirmed_slot(messages)):
            # Nudge: the read back got a yes, so book now instead of asking again.
            messages.append({"role": "system", "content": confirmed_note(*slot)})
            yield Event("nudge", text=f"{slot[0]} {slot[1]}")
        done: set[str] = set()  # "book" / "cancel": tool succeeded in this turn
        results: dict[str, dict] = {}  # executed calls in this turn, by name + normalized args
        retried = False
        round_no = 0
        while round_no <= MAX_TOOL_ROUNDS:
            timing.rounds += 1
            splitter = SentenceSplitter()
            spoken: list[str] = []
            blocked: str | None = None  # claim kind that tripped the guard
            tool_calls: list[dict] = []
            use_tools = ollama_tools if round_no < MAX_TOOL_ROUNDS else []

            def check(sentences: list[str]) -> list[str]:
                nonlocal blocked
                ok = []
                for s in sentences:
                    s = strip_placeholders(s)
                    if not s:
                        continue
                    if blocked is None:
                        kind = claim_kind(s)
                        if kind and kind not in done:
                            blocked = kind  # drop this sentence and the rest of the round
                    if blocked is None:
                        ok.append(s)
                return ok

            async for chunk in self._chat_stream(messages, use_tools):
                msg = chunk.get("message") or {}
                piece = msg.get("content") or ""
                if piece:
                    for s in check(splitter.feed(piece)):
                        if timing.first_sentence_ms is None:
                            timing.first_sentence_ms = int((time.perf_counter() - t0) * 1000)
                        spoken.append(s)
                        yield Event("say", text=s)
                tool_calls.extend(msg.get("tool_calls") or [])
            for s in check(splitter.flush()):
                if timing.first_sentence_ms is None:
                    timing.first_sentence_ms = int((time.perf_counter() - t0) * 1000)
                spoken.append(s)
                yield Event("say", text=s)

            if blocked and not tool_calls:
                yield Event("guard", text=blocked)
                if not retried:
                    retried = True
                    if spoken:
                        messages.append({"role": "assistant", "content": " ".join(spoken)})
                    messages.append({"role": "system", "content": GUARD_NOTE[blocked]})
                    continue  # ask again; does not use up a tool round
                fallback = GUARD_FALLBACK.get(language, GUARD_FALLBACK["en"])
                messages.append({"role": "assistant", "content": " ".join(spoken + [fallback])})
                yield Event("say", text=fallback)
                break

            # History holds only what was spoken, never a blocked claim.
            assistant = {"role": "assistant", "content": " ".join(spoken)}
            if tool_calls:
                assistant["tool_calls"] = tool_calls
            messages.append(assistant)
            if not tool_calls:
                break
            round_no += 1
            for tc in tool_calls:
                fn = tc.get("function") or {}
                name = fn.get("name", "")
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                key = _call_key(name, args)
                if key in results:
                    # The model repeated an identical call in this turn: reuse the first result, so
                    # a second book_appointment cannot fail with slot_not_available.
                    yield Event("dup", name=name, args=args, result=results[key])
                    messages.append({"role": "tool", "tool_name": name, "content": json.dumps(results[key])})
                    continue
                if self.confirm_gate and name == "book_appointment" and not confirmation_ok(messages, args):
                    result = dict(CONFIRM_FIRST)  # the mock tool is never called
                    yield Event("gate", name=name, args=args, result=result)
                    messages.append({"role": "tool", "tool_name": name, "content": json.dumps(result)})
                    continue
                result = await tools.call(name, args)
                results[key] = result
                if isinstance(result, dict) and result.get("ok") and name in _CLAIM_TOOLS:
                    done.add(_CLAIM_TOOLS[name])
                yield Event("tool", name=name, args=args, result=result)
                messages.append({"role": "tool", "tool_name": name, "content": json.dumps(result)})
        timing.total_ms = int((time.perf_counter() - t0) * 1000)
