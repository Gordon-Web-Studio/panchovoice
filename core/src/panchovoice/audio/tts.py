"""Text to speech for the caller voice, with a disk cache.

Providers:
- kokoro: a local Kokoro OpenAI compatible HTTP server
- elevenlabs: the ElevenLabs cloud API (key in ELEVENLABS_API_KEY)
- deepgram: Deepgram Aura in the cloud (key in DEEPGRAM_API_KEY)

Every provider returns int16 mono 16 kHz speech with silence trimmed. Each line is cached on disk by
provider, model, voice, speed and text, so a cloud provider is billed once per line, not once per run.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import httpx
import numpy as np

from .pcm import from_bytes, read_wav, tone, trim_silence, wav_to_pcm16, write_wav

if TYPE_CHECKING:
    from ..config import TTSConfig


class TTSError(RuntimeError):
    pass


class TTS(Protocol):
    def synth(self, text: str, voice: str, speed: float = 1.0) -> np.ndarray:
        """Return int16 mono 16 kHz speech with leading and trailing silence trimmed."""
        ...


# Scenario files name Kokoro voices (af_heart, em_alex, ...). A cloud provider maps them through
# tts.voices, and falls back to tts.default_voice for any other Kokoro style id.
KOKORO_VOICE = re.compile(r"^[a-z]{2}_[a-z0-9]+$")


class _CachedTTS:
    provider = ""

    def __init__(self, cache_dir: str | Path | None, timeout: float, client: httpx.Client | None):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.timeout = timeout
        self._client = client
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key(self, text: str, voice: str, speed: float) -> str:
        raise NotImplementedError

    def _fetch(self, text: str, voice: str, speed: float) -> np.ndarray:
        raise NotImplementedError

    def _post(self, url: str, **kw) -> httpx.Response:
        try:
            if self._client is not None:
                return self._client.post(url, timeout=self.timeout, **kw)
            return httpx.post(url, timeout=self.timeout, **kw)
        except httpx.HTTPError as exc:
            raise TTSError(f"{self.provider} TTS not reachable at {getattr(self, 'url', '?')}: {exc}") from exc

    def synth(self, text: str, voice: str, speed: float = 1.0) -> np.ndarray:
        cache_file = self.cache_dir / f"{self._cache_key(text, voice, speed)}.wav" if self.cache_dir else None
        if cache_file and cache_file.is_file():
            samples, _ = read_wav(cache_file)
            return samples
        samples = trim_silence(self._fetch(text, voice, speed))
        if len(samples) == 0:
            raise TTSError(f"{self.provider} returned silence for {text!r}")
        if cache_file:
            write_wav(cache_file, samples)
        return samples


class KokoroTTS(_CachedTTS):
    provider = "Kokoro"

    def __init__(self, url: str = "http://localhost:8880", cache_dir: str | Path | None = None, timeout: float = 60.0,
                 client: httpx.Client | None = None):
        super().__init__(cache_dir, timeout, client)
        self.url = url.rstrip("/")

    @staticmethod
    def cache_key(text: str, voice: str, speed: float) -> str:
        return hashlib.sha256(f"kokoro|{voice}|{speed:.3f}|{text}".encode("utf-8")).hexdigest()[:32]

    def _cache_key(self, text: str, voice: str, speed: float) -> str:
        return self.cache_key(text, voice, speed)

    def _fetch(self, text: str, voice: str, speed: float) -> np.ndarray:
        body = {"model": "kokoro", "input": text, "voice": voice, "response_format": "wav", "speed": speed}
        r = self._post(f"{self.url}/v1/audio/speech", json=body)
        if r.status_code != 200:
            raise TTSError(f"Kokoro TTS error {r.status_code}: {r.text[:300]}")
        return wav_to_pcm16(r.content)

    def voices(self) -> list[str]:
        r = httpx.get(f"{self.url}/v1/audio/voices", timeout=10)
        r.raise_for_status()
        data = r.json()
        items = data.get("voices", data) if isinstance(data, dict) else data
        return [v["id"] if isinstance(v, dict) else str(v) for v in items]


class _CloudTTS(_CachedTTS):
    """Shared parts of the cloud providers: API key, model, and the scenario voice -> provider voice map."""

    def __init__(self, api_key: str, url: str, model: str, default_voice: str, voices: dict[str, str] | None = None,
                 cache_dir: str | Path | None = None, timeout: float = 60.0, client: httpx.Client | None = None):
        super().__init__(cache_dir, timeout, client)
        if not api_key:
            raise TTSError(f"{self.provider} needs an API key")
        self.api_key = api_key
        self.url = url.rstrip("/")
        self.model = model
        self.default_voice = default_voice
        self.voices = dict(voices or {})

    def resolve_voice(self, voice: str) -> str:
        """Scenario voice -> provider voice: the voices map, then default_voice for Kokoro ids, else as is."""
        if voice in self.voices:
            return self.voices[voice]
        if not voice or KOKORO_VOICE.match(voice):
            return self.default_voice
        return voice

    def _cache_key(self, text: str, voice: str, speed: float) -> str:
        v = self.resolve_voice(voice)
        raw = f"{self.provider.lower()}|{self.model}|{v}|{speed:.3f}|{text}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def _check(self, r: httpx.Response) -> None:
        if r.status_code in (401, 403):
            raise TTSError(f"{self.provider} rejected the API key ({r.status_code}): {r.text[:300]}")
        if r.status_code != 200:
            raise TTSError(f"{self.provider} TTS error {r.status_code}: {r.text[:300]}")


class ElevenLabsTTS(_CloudTTS):
    provider = "ElevenLabs"

    def _fetch(self, text: str, voice: str, speed: float) -> np.ndarray:
        v = self.resolve_voice(voice)
        body: dict = {"text": text, "model_id": self.model}
        if abs(speed - 1.0) > 1e-3:
            # ElevenLabs accepts 0.7 to 1.2
            body["voice_settings"] = {"speed": max(0.7, min(1.2, speed))}
        r = self._post(f"{self.url}/v1/text-to-speech/{v}", params={"output_format": "pcm_16000"},
                       headers={"xi-api-key": self.api_key}, json=body)
        self._check(r)
        return from_bytes(r.content)  # raw s16le, 16 kHz, mono


class DeepgramTTS(_CloudTTS):
    """Deepgram Aura. The voice is the model name (aura-2-thalia-en, ...), so `model` is not used."""

    provider = "Deepgram"

    def _fetch(self, text: str, voice: str, speed: float) -> np.ndarray:
        v = self.resolve_voice(voice)
        params = {"model": v, "encoding": "linear16", "sample_rate": 16000, "container": "none"}
        r = self._post(f"{self.url}/v1/speak", params=params, headers={"Authorization": f"Token {self.api_key}"},
                       json={"text": text})
        self._check(r)
        return from_bytes(r.content)


class ToneTTS:
    """Fake TTS for tests: a tone whose length grows with the text (60 ms per character, 400 ms minimum)."""

    def __init__(self, ms_per_char: int = 60, min_ms: int = 400, max_ms: int = 4000):
        self.ms_per_char = ms_per_char
        self.min_ms = min_ms
        self.max_ms = max_ms

    def synth(self, text: str, voice: str = "", speed: float = 1.0) -> np.ndarray:
        ms = max(self.min_ms, min(self.max_ms, int(len(text) * self.ms_per_char / max(speed, 0.1))))
        return tone(ms, freq=300.0 + (len(text) % 7) * 40)


def make_tts(cfg: "TTSConfig", cache_dir: str | Path | None = None, client: httpx.Client | None = None) -> TTS:
    """The caller voice from the tts section of panchovoice.yaml."""
    if cfg.provider == "kokoro":
        return KokoroTTS(cfg.url, cache_dir, client=client)
    key = os.environ.get(cfg.key_env, "")
    if not key:
        raise TTSError(f"tts.provider is {cfg.provider} but {cfg.key_env} is not set. "
                       "Set it in your shell (never in panchovoice.yaml)")
    cls = ElevenLabsTTS if cfg.provider == "elevenlabs" else DeepgramTTS
    return cls(key, cfg.url, cfg.model or "", cfg.default_voice or "", cfg.voices, cache_dir, client=client)
