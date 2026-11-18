"""Speech to text with faster-whisper (cpu, int8, lazy singleton per model name)."""

from __future__ import annotations

import threading

import numpy as np

from .pcm import to_float

_models: dict[str, object] = {}
_lock = threading.Lock()


def get_model(name: str = "small"):
    with _lock:
        if name not in _models:
            from faster_whisper import WhisperModel

            _models[name] = WhisperModel(name, device="cpu", compute_type="int8")
        return _models[name]


def transcribe(samples: np.ndarray, language: str | None = "en", model: str = "small") -> str:
    """int16 16 kHz mono -> text. Empty audio gives an empty string."""
    if len(samples) < 1600:  # under 100 ms
        return ""
    m = get_model(model)
    segments, _info = m.transcribe(to_float(samples), language=language, beam_size=1, vad_filter=False,
                                   condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segments).strip()
