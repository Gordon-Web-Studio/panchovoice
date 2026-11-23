"""Speech to text inside the harness: what the agent said, when the agent does not send its own transcript.

Providers:
- faster-whisper: local, cpu, int8, lazy singleton per model name
- deepgram: the Deepgram cloud API (key in DEEPGRAM_API_KEY)
"""

from __future__ import annotations

import io
import os
import threading
import wave
from typing import TYPE_CHECKING, Callable

import httpx
import numpy as np

from .pcm import SAMPLE_RATE, to_float

if TYPE_CHECKING:
    from ..config import STTConfig

# (int16 16 kHz mono samples, language) -> text
Transcriber = Callable[[np.ndarray, "str | None"], str]

MIN_SAMPLES = 1600  # under 100 ms: nothing to transcribe

_models: dict[str, object] = {}
_lock = threading.Lock()


class STTError(RuntimeError):
    pass


def get_model(name: str = "small"):
    with _lock:
        if name not in _models:
            from faster_whisper import WhisperModel

            _models[name] = WhisperModel(name, device="cpu", compute_type="int8")
        return _models[name]


def transcribe(samples: np.ndarray, language: str | None = "en", model: str = "small") -> str:
    """faster-whisper: int16 16 kHz mono -> text. Empty audio gives an empty string."""
    if len(samples) < MIN_SAMPLES:
        return ""
    m = get_model(model)
    segments, _info = m.transcribe(to_float(samples), language=language, beam_size=1, vad_filter=False,
                                   condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segments).strip()


def _wav_bytes(samples: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(np.asarray(samples, dtype="<i2").tobytes())
    return buf.getvalue()


class DeepgramSTT:
    """Deepgram pre-recorded transcription (POST /v1/listen with a wav body)."""

    def __init__(self, api_key: str, model: str = "nova-3", url: str = "https://api.deepgram.com",
                 timeout: float = 30.0, client: httpx.Client | None = None):
        if not api_key:
            raise STTError("Deepgram needs an API key")
        self.api_key = api_key
        self.model = model
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._client = client

    def __call__(self, samples: np.ndarray, language: str | None = "en") -> str:
        if len(samples) < MIN_SAMPLES:
            return ""
        params = {"model": self.model, "punctuate": "true"}
        if language:
            params["language"] = language
        kw = dict(params=params, content=_wav_bytes(samples), timeout=self.timeout,
                  headers={"Authorization": f"Token {self.api_key}", "Content-Type": "audio/wav"})
        try:
            r = (self._client.post if self._client is not None else httpx.post)(f"{self.url}/v1/listen", **kw)
        except httpx.HTTPError as exc:
            raise STTError(f"Deepgram STT not reachable at {self.url}: {exc}") from exc
        if r.status_code in (401, 403):
            raise STTError(f"Deepgram rejected the API key ({r.status_code}): {r.text[:300]}")
        if r.status_code != 200:
            raise STTError(f"Deepgram STT error {r.status_code}: {r.text[:300]}")
        try:
            alts = r.json()["results"]["channels"][0]["alternatives"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise STTError(f"Deepgram gave an unexpected answer: {r.text[:300]}") from exc
        return str(alts[0].get("transcript") or "").strip() if alts else ""


def make_transcriber(cfg: "STTConfig", client: httpx.Client | None = None) -> Transcriber:
    """The harness speech to text from the stt section of panchovoice.yaml."""
    if cfg.provider == "deepgram":
        key = os.environ.get(cfg.key_env, "")
        if not key:
            raise STTError(f"stt.provider is deepgram but {cfg.key_env} is not set. "
                           "Set it in your shell (never in panchovoice.yaml)")
        return DeepgramSTT(key, cfg.model or "nova-3", cfg.url or "https://api.deepgram.com", client=client)
    model = cfg.model or "small"
    return lambda samples, language="en": transcribe(samples, language, model)
