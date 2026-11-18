"""Metrics from the recording: per turn latency, percentiles, WER, barge-in stop time (contract section 6)."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

from .audio.pcm import ms_to_samples, samples_to_ms
from .audio.vad import Segment, speech_segments


@dataclass
class TurnTiming:
    caller_start_ms: int
    caller_end_ms: int
    next_caller_start_ms: int | None  # end of this turn's window (None = end of call)
    interrupted: bool = False


@dataclass
class TurnMeasure:
    agent_start_ms: int | None
    agent_end_ms: int | None
    latency_ms: int | None
    stop_ms: int | None


def percentile(values: Sequence[float], p: float) -> float | None:
    """Nearest rank percentile: the value at rank ceil(p/100 * n) of the sorted list."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    rank = max(1, math.ceil(p / 100 * len(vals)))
    return vals[min(rank, len(vals)) - 1]


def measure_turns(agent_track: np.ndarray, turns: list[TurnTiming], vad: str = "silero",
                  segments: list[Segment] | None = None) -> list[TurnMeasure]:
    """Latency per turn = first agent speech onset after caller_end_ms minus caller_end_ms.

    The onset must come before the next caller turn starts. For interrupted turns,
    stop_ms = end of the agent speech that was running when the caller started, minus caller_start_ms.
    """
    segs = segments if segments is not None else speech_segments(agent_track, vad)
    segs_ms = [(samples_to_ms(s), samples_to_ms(e)) for s, e in segs]
    out: list[TurnMeasure] = []
    for t in turns:
        win_end = t.next_caller_start_ms if t.next_caller_start_ms is not None else 10**12
        onset = next((s for s, e in segs_ms if t.caller_end_ms <= s < win_end), None)
        in_window = [(s, e) for s, e in segs_ms if s < win_end and e > t.caller_end_ms]
        agent_end = min(in_window[-1][1], win_end) if in_window and onset is not None else None
        stop = None
        if t.interrupted:
            running = next((e for s, e in segs_ms if s <= t.caller_start_ms < e), None)
            stop = (running - t.caller_start_ms) if running is not None else None
        out.append(TurnMeasure(
            agent_start_ms=onset,
            agent_end_ms=agent_end,
            latency_ms=(onset - t.caller_end_ms) if onset is not None else None,
            stop_ms=stop,
        ))
    return out


def latency_summary(latencies: Iterable[int | None]) -> dict[str, int | None]:
    vals = [v for v in latencies if v is not None]
    if not vals:
        return {"latency_p50_ms": None, "latency_p95_ms": None, "latency_max_ms": None}
    return {
        "latency_p50_ms": int(percentile(vals, 50)),
        "latency_p95_ms": int(percentile(vals, 95)),
        "latency_max_ms": int(max(vals)),
    }


# --------------------------------------------------------------------------- WER

_NUM_EN = {
    "zero": 0, "oh": None, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_NUM_ES = {
    "cero": 0, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
    "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20, "treinta": 30,
    "cuarenta": 40, "cincuenta": 50,
}
_NUMS = {k: v for k, v in {**_NUM_EN, **_NUM_ES}.items() if v is not None}
_TENS = {20, 30, 40, 50, 60, 70, 80, 90}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_text(text: str) -> str:
    """Lowercase, no accents, no punctuation, simple number words as digits ("twenty one" -> "21")."""
    s = _strip_accents((text or "").lower())
    s = s.replace("'", "").replace("’", "")
    s = re.sub(r"(\d)[:.](\d)", r"\1 \2", s)  # 10:00 -> 10 00
    s = re.sub(r"\b([ap])\s?\.\s?m\b\.?", r"\1m", s)  # a.m. / p. m. -> am / pm
    s = re.sub(r"[^\w\s]", " ", s)
    s = s.replace("_", " ")
    words = s.split()
    out: list[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        if w in _NUMS:
            val = _NUMS[w]
            if val in _TENS and i + 1 < len(words) and words[i + 1] in _NUMS and 0 < _NUMS[words[i + 1]] < 10:
                val += _NUMS[words[i + 1]]
                i += 1
            out.append(str(val))
        elif w in ("am", "pm") and out and out[-1].isdigit():
            out.append(w)
        else:
            out.append(w)
        i += 1
    # "10 00" and "10" are the same time when said aloud: drop a trailing "00" after a number
    cleaned: list[str] = []
    for w in out:
        if w == "00" and cleaned and cleaned[-1].isdigit():
            continue
        cleaned.append(w)
    return " ".join(cleaned)


def wer(references: Sequence[str], hypotheses: Sequence[str]) -> float | None:
    """Corpus WER over pairs (reference = what the caller said, hypothesis = what the agent heard)."""
    import jiwer

    pairs = [(normalize_text(r), normalize_text(h)) for r, h in zip(references, hypotheses)]
    pairs = [(r, h) for r, h in pairs if r]
    if not pairs:
        return None
    refs = [r for r, _ in pairs]
    hyps = [h if h else "" for _, h in pairs]
    # jiwer rejects empty hypotheses in some versions: count them as all deletions
    errors = 0
    words = 0
    for r, h in zip(refs, hyps):
        n = len(r.split())
        words += n
        if not h:
            errors += n
        else:
            out = jiwer.process_words(r, h)
            errors += out.substitutions + out.deletions + out.insertions
    return round(errors / words, 4) if words else None
