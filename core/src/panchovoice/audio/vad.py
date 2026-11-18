"""Speech segments: Silero VAD (the onnx model bundled with faster-whisper) or a simple energy detector."""

from __future__ import annotations

import numpy as np

from .pcm import SAMPLE_RATE, ms_to_samples, to_float

Segment = tuple[int, int]  # (start_sample, end_sample)


def silero_segments(samples: np.ndarray, threshold: float = 0.5, min_silence_ms: int = 300,
                    min_speech_ms: int = 120, pad_ms: int = 0) -> list[Segment]:
    """Speech segments of int16 16 kHz audio. pad_ms is 0 so onsets are not moved earlier."""
    if len(samples) == 0:
        return []
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    opts = VadOptions(
        threshold=threshold,
        min_silence_duration_ms=min_silence_ms,
        min_speech_duration_ms=min_speech_ms,
        speech_pad_ms=pad_ms,
    )
    stamps = get_speech_timestamps(to_float(samples), opts, sampling_rate=SAMPLE_RATE)
    segs = [(int(s["start"]), int(s["end"])) for s in stamps]
    return [(refine_onset(samples, s), e) for s, e in segs]


def refine_onset(samples: np.ndarray, start: int, max_back_ms: int = 250, floor_dbfs: float = -50.0,
                 win_ms: int = 5) -> int:
    """Silero works on 32 ms windows and reacts late to soft onsets (s, f, h).

    Walk back from its onset while the audio is still above the floor, at most `max_back_ms`.
    """
    win = ms_to_samples(win_ms)
    limit = max(0, start - ms_to_samples(max_back_ms))
    pos = start
    while pos - win >= limit:
        x = to_float(samples[pos - win:pos])
        if 10 * np.log10(np.mean(x * x) + 1e-12) <= floor_dbfs:
            break
        pos -= win
    return pos


def energy_segments(samples: np.ndarray, threshold_dbfs: float = -40.0, win_ms: int = 10,
                    min_silence_ms: int = 300, min_speech_ms: int = 60) -> list[Segment]:
    """Energy based segments, for synthetic test audio (tones) that Silero does not call speech."""
    if len(samples) == 0:
        return []
    win = ms_to_samples(win_ms)
    n = len(samples) // win
    if n == 0:
        return []
    x = to_float(samples[: n * win]).reshape(n, win)
    loud = 10 * np.log10(np.mean(x * x, axis=1) + 1e-12) > threshold_dbfs
    segs: list[list[int]] = []
    gap = max(1, min_silence_ms // win_ms)
    i = 0
    while i < n:
        if loud[i]:
            start = i
            last = i
            j = i + 1
            while j < n and (j - last) <= gap:
                if loud[j]:
                    last = j
                j += 1
            segs.append([start, last + 1])
            i = last + 1
        else:
            i += 1
    min_w = max(1, min_speech_ms // win_ms)
    return [(s * win, e * win) for s, e in segs if e - s >= min_w]


def speech_segments(samples: np.ndarray, method: str = "silero") -> list[Segment]:
    if method == "energy":
        return energy_segments(samples)
    if method == "silero":
        return silero_segments(samples)
    raise ValueError(f"unknown vad method {method!r}")
