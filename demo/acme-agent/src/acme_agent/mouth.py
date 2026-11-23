"""Mouth: text to speech through an OpenAI compatible Kokoro server, resampled to 16 kHz."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx
import numpy as np

KOKORO_RATE = 24000  # kokoro-fastapi returns raw PCM16 at 24 kHz for response_format "pcm"

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    """Split a reply into sentences so the first one can be spoken sooner.

    Very short pieces are merged into the next one (Kokoro sounds odd on 1 or 2 words).
    """
    parts = [p.strip() for p in _SENTENCE_END.split(text.strip()) if p.strip()]
    out: list[str] = []
    carry = ""
    for p in parts:
        piece = f"{carry} {p}".strip() if carry else p
        if len(piece.split()) < 3:
            carry = piece
            continue
        out.append(piece)
        carry = ""
    if carry:
        if out:
            out[-1] = f"{out[-1]} {carry}"
        else:
            out.append(carry)
    return out


class Resampler24to16:
    """Streaming 24 kHz to 16 kHz PCM16 resampler (3 samples in, 2 out, linear interpolation).

    Accepts byte chunks of any size; keeps the leftover bytes and samples between calls.
    """

    def __init__(self) -> None:
        self._bytes = b""
        self._samples = np.zeros(0, dtype=np.float32)

    def feed(self, data: bytes) -> bytes:
        data = self._bytes + data
        n = len(data) // 2 * 2
        self._bytes = data[n:]
        x = np.concatenate([self._samples, np.frombuffer(data[:n], dtype="<i2").astype(np.float32)])
        usable = len(x) // 3 * 3
        self._samples = x[usable:]
        if usable == 0:
            return b""
        g = x[:usable].reshape(-1, 3)
        out = np.empty((g.shape[0], 2), dtype=np.float32)
        out[:, 0] = g[:, 0]
        out[:, 1] = (g[:, 1] + g[:, 2]) * 0.5  # output time 1.5 input samples
        return np.clip(out.reshape(-1), -32768, 32767).astype("<i2").tobytes()

    def flush(self) -> bytes:
        rest = self._samples
        self._samples = np.zeros(0, dtype=np.float32)
        self._bytes = b""
        if len(rest) == 0:
            return b""
        idx = np.arange(0, len(rest), 1.5)
        y = np.interp(idx, np.arange(len(rest)), rest)
        return np.clip(y, -32768, 32767).astype("<i2").tobytes()


def resample_24k_to_16k(pcm24: bytes) -> bytes:
    r = Resampler24to16()
    return r.feed(pcm24) + r.flush()


class Mouth:
    """Streams 16 kHz PCM16 audio for a piece of text from Kokoro."""

    def __init__(self, client: httpx.AsyncClient, url: str, voice: str = "af_heart", speed: float = 1.0) -> None:
        self.client = client
        self.url = url.rstrip("/")
        self.voice = voice
        self.speed = speed

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        body = {
            "model": "kokoro",
            "input": text,
            "voice": self.voice,
            "response_format": "pcm",
            "speed": self.speed,
            "stream": True,
        }
        r = Resampler24to16()
        async with self.client.stream("POST", f"{self.url}/v1/audio/speech", json=body, timeout=60) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                out = r.feed(chunk)
                if out:
                    yield out
        tail = r.flush()
        if tail:
            yield tail


class DeepgramMouth:
    """Streams 16 kHz PCM16 audio for a piece of text from Deepgram Aura (no resampling needed)."""

    def __init__(self, client: httpx.AsyncClient, api_key: str, voice: str = "aura-2-thalia-en",
                 url: str = "https://api.deepgram.com") -> None:
        self.client = client
        self.api_key = api_key
        self.voice = voice
        self.url = url.rstrip("/")

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        params = {"model": self.voice, "encoding": "linear16", "sample_rate": "16000", "container": "none"}
        headers = {"Authorization": f"Token {self.api_key}"}
        carry = b""
        async with self.client.stream("POST", f"{self.url}/v1/speak", params=params, json={"text": text},
                                      headers=headers, timeout=60) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                resp.raise_for_status()  # httpx.HTTPStatusError: logged as a TTS error by the call
            async for chunk in resp.aiter_bytes():
                data = carry + chunk
                n = len(data) // 2 * 2  # keep whole 16 bit samples
                carry = data[n:]
                if n:
                    yield data[:n]


def make_mouth(client: httpx.AsyncClient, settings) -> "Mouth | DeepgramMouth":
    if settings.tts == "deepgram":
        return DeepgramMouth(client, settings.deepgram_api_key, settings.deepgram_voice)
    return Mouth(client, settings.kokoro_url, settings.voice)
