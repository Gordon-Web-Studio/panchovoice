"""Ears: streaming voice activity detection, end of turn detection, and speech to text.

Audio in is PCM16 LE mono 16 kHz. The VAD is the Silero ONNX model that ships inside
faster-whisper, so no torch is needed.
"""

from __future__ import annotations

import collections
import io
import logging
import threading
import wave
from dataclasses import dataclass, field

import numpy as np

SAMPLE_RATE = 16000
VAD_FRAME = 512  # samples per VAD step (32 ms at 16 kHz)
VAD_FRAME_MS = VAD_FRAME * 1000 // SAMPLE_RATE  # 32
_CONTEXT = 64  # Silero needs the last 64 samples of the previous frame


class StreamingVad:
    """Silero VAD with state kept between frames. Returns a speech probability per 512 samples."""

    def __init__(self) -> None:
        from faster_whisper.vad import get_vad_model

        self._session = get_vad_model().session  # shared ONNX session, loaded once
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(_CONTEXT, dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        """frame: float32 array of exactly 512 samples in [-1, 1]."""
        x = np.concatenate([self._context, frame]).reshape(1, -1).astype(np.float32)
        out, self._h, self._c = self._session.run(None, {"input": x, "h": self._h, "c": self._c})
        self._context = frame[-_CONTEXT:]
        return float(np.asarray(out).reshape(-1)[0])


@dataclass
class EndpointEvent:
    kind: str  # "start" | "end" | "blip"
    audio: np.ndarray | None = None  # float32 utterance audio for "end"
    voiced_ms: int = 0


@dataclass
class Endpointer:
    """Turns per frame speech probabilities into utterances.

    - Speech starts after `start_ms` of consecutive speech frames.
    - The turn ends after `end_silence_ms` of silence.
    - Utterances with less than `min_voiced_ms` of speech are dropped as blips.
    """

    threshold: float = 0.5
    neg_threshold: float = 0.35
    start_ms: int = 96
    end_silence_ms: int = 700
    min_voiced_ms: int = 250
    preroll_ms: int = 320

    in_speech: bool = False
    voiced_ms: int = 0  # speech ms inside the current utterance
    epoch: int = 0  # changes every time speech starts or comes back after a pause
    _run_ms: int = 0
    _silence_ms: int = 0
    _frames: list = field(default_factory=list)
    _preroll: collections.deque = field(default_factory=collections.deque)

    def process(self, frame: np.ndarray, prob: float) -> EndpointEvent | None:
        if not self.in_speech:
            self._preroll.append(frame)
            while len(self._preroll) * VAD_FRAME_MS > self.preroll_ms:
                self._preroll.popleft()
            self._run_ms = self._run_ms + VAD_FRAME_MS if prob >= self.threshold else 0
            if self._run_ms >= self.start_ms:
                self.in_speech = True
                self.epoch += 1
                self.voiced_ms = self._run_ms
                self._silence_ms = 0
                self._frames = list(self._preroll)
                self._preroll.clear()
                return EndpointEvent("start", voiced_ms=self.voiced_ms)
            return None

        self._frames.append(frame)
        if prob >= self.neg_threshold:
            if self._silence_ms:
                self.epoch += 1
            self._silence_ms = 0
            if prob >= self.threshold:
                self.voiced_ms += VAD_FRAME_MS
        else:
            self._silence_ms += VAD_FRAME_MS
        if self._silence_ms < self.end_silence_ms:
            return None

        # End of turn.
        audio = np.concatenate(self._frames) if self._frames else np.zeros(0, np.float32)
        voiced = self.voiced_ms
        self.in_speech = False
        self.voiced_ms = 0
        self._run_ms = 0
        self._frames = []
        if voiced < self.min_voiced_ms:
            return EndpointEvent("blip", voiced_ms=voiced)
        # Trim most of the trailing silence, keep 200 ms.
        keep = max(0, len(audio) - (self._silence_ms - 200) * SAMPLE_RATE // 1000)
        return EndpointEvent("end", audio=audio[:keep], voiced_ms=voiced)

    @property
    def silence_ms(self) -> int:
        return self._silence_ms if self.in_speech else 0

    def snapshot(self) -> np.ndarray:
        """Audio of the utterance so far, trailing silence trimmed to 200 ms (for speculative STT)."""
        audio = np.concatenate(self._frames) if self._frames else np.zeros(0, np.float32)
        keep = max(0, len(audio) - max(0, self._silence_ms - 200) * SAMPLE_RATE // 1000)
        return audio[:keep]


class Ears:
    """Byte stream in, endpoint events out. One instance per call."""

    def __init__(self, endpointer: Endpointer | None = None, vad=None) -> None:
        self.vad = vad if vad is not None else StreamingVad()
        self.endpointer = endpointer or Endpointer()
        self._pending = b""

    def feed(self, pcm: bytes) -> list[EndpointEvent]:
        self._pending += pcm
        events = []
        step = VAD_FRAME * 2
        while len(self._pending) >= step:
            chunk, self._pending = self._pending[:step], self._pending[step:]
            frame = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
            ev = self.endpointer.process(frame, self.vad(frame))
            if ev is not None:
                events.append(ev)
        return events


class Transcriber:
    """faster-whisper on CPU with int8. Load once at startup, share between calls."""

    def __init__(self, model: str = "small") -> None:
        from faster_whisper import WhisperModel

        self.model = WhisperModel(model, device="cpu", compute_type="int8")
        self._lock = threading.Lock()

    def transcribe(self, audio: np.ndarray, language: str = "en") -> str:
        with self._lock:
            segments, _ = self.model.transcribe(
                audio,
                language=language,
                beam_size=1,
                vad_filter=False,
                condition_on_previous_text=False,
                without_timestamps=True,
            )
            return " ".join(s.text.strip() for s in segments).strip()


DEEPGRAM_URL = "https://api.deepgram.com"


class DeepgramTranscriber:
    """Deepgram speech to text (pre-recorded endpoint), same interface as Transcriber.

    The key comes from the caller (read from DEEPGRAM_API_KEY in cli.py), never from a file.
    """

    def __init__(self, api_key: str, model: str = "nova-3", url: str = DEEPGRAM_URL, timeout: float = 30.0,
                 client=None) -> None:
        import httpx

        self.api_key = api_key
        self.model = model
        self.url = url.rstrip("/")
        self.client = client or httpx.Client(timeout=timeout)
        self.strict = True  # raise on errors (startup check); set False once the server runs

    @staticmethod
    def _wav(audio: np.ndarray) -> bytes:
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm)
        return buf.getvalue()

    def transcribe(self, audio: np.ndarray, language: str = "en") -> str:
        if len(audio) < SAMPLE_RATE // 10:  # under 100 ms: nothing to send
            return ""
        params = {"model": self.model, "language": language or "en", "punctuate": "true", "smart_format": "true"}
        try:
            r = self.client.post(f"{self.url}/v1/listen", params=params, content=self._wav(audio),
                                 headers={"Authorization": f"Token {self.api_key}", "Content-Type": "audio/wav"})
            if r.status_code in (401, 403):
                raise RuntimeError("Deepgram rejected DEEPGRAM_API_KEY (HTTP %d)" % r.status_code)
            r.raise_for_status()
            alts = r.json()["results"]["channels"][0]["alternatives"]
            return (alts[0].get("transcript") or "").strip() if alts else ""
        except Exception as e:
            if self.strict:
                raise
            logging.getLogger("acme_agent").warning("Deepgram STT error: %s", e)
            return ""
