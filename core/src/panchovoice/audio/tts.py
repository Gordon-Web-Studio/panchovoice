"""Text to speech through a Kokoro OpenAI compatible HTTP server, with a disk cache."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

import httpx
import numpy as np

from .pcm import read_wav, tone, trim_silence, wav_to_pcm16, write_wav


class TTSError(RuntimeError):
    pass


class TTS(Protocol):
    def synth(self, text: str, voice: str, speed: float = 1.0) -> np.ndarray:
        """Return int16 mono 16 kHz speech with leading and trailing silence trimmed."""
        ...


class KokoroTTS:
    def __init__(self, url: str = "http://localhost:8880", cache_dir: str | Path | None = None, timeout: float = 60.0):
        self.url = url.rstrip("/")
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.timeout = timeout
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def cache_key(text: str, voice: str, speed: float) -> str:
        return hashlib.sha256(f"kokoro|{voice}|{speed:.3f}|{text}".encode("utf-8")).hexdigest()[:32]

    def synth(self, text: str, voice: str, speed: float = 1.0) -> np.ndarray:
        cache_file = self.cache_dir / f"{self.cache_key(text, voice, speed)}.wav" if self.cache_dir else None
        if cache_file and cache_file.is_file():
            samples, _ = read_wav(cache_file)
            return samples
        body = {"model": "kokoro", "input": text, "voice": voice, "response_format": "wav", "speed": speed}
        try:
            r = httpx.post(f"{self.url}/v1/audio/speech", json=body, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise TTSError(f"Kokoro TTS not reachable at {self.url}: {exc}") from exc
        if r.status_code != 200:
            raise TTSError(f"Kokoro TTS error {r.status_code}: {r.text[:300]}")
        samples = trim_silence(wav_to_pcm16(r.content))
        if len(samples) == 0:
            raise TTSError(f"Kokoro returned silence for {text!r}")
        if cache_file:
            write_wav(cache_file, samples)
        return samples

    def voices(self) -> list[str]:
        r = httpx.get(f"{self.url}/v1/audio/voices", timeout=10)
        r.raise_for_status()
        data = r.json()
        items = data.get("voices", data) if isinstance(data, dict) else data
        return [v["id"] if isinstance(v, dict) else str(v) for v in items]


class ToneTTS:
    """Fake TTS for tests: a tone whose length grows with the text (60 ms per character, 400 ms minimum)."""

    def __init__(self, ms_per_char: int = 60, min_ms: int = 400, max_ms: int = 4000):
        self.ms_per_char = ms_per_char
        self.min_ms = min_ms
        self.max_ms = max_ms

    def synth(self, text: str, voice: str = "", speed: float = 1.0) -> np.ndarray:
        ms = max(self.min_ms, min(self.max_ms, int(len(text) * self.ms_per_char / max(speed, 0.1))))
        return tone(ms, freq=300.0 + (len(text) % 7) * 40)
