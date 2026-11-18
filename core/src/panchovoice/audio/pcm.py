"""PCM helpers: conversion, wav io, resampling, trimming."""

from __future__ import annotations

import io
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 320
FRAME_BYTES = FRAME_SAMPLES * 2  # 640


def ms_to_samples(ms: float) -> int:
    return int(round(ms * SAMPLE_RATE / 1000))


def samples_to_ms(n: int) -> int:
    return int(round(n * 1000 / SAMPLE_RATE))


def to_bytes(samples: np.ndarray) -> bytes:
    return np.asarray(samples, dtype="<i2").tobytes()


def from_bytes(data: bytes) -> np.ndarray:
    if len(data) % 2:
        data = data[:-1]
    return np.frombuffer(data, dtype="<i2").astype(np.int16)


def to_float(samples: np.ndarray) -> np.ndarray:
    return np.asarray(samples, dtype=np.float32) / 32768.0


def to_int16(x: np.ndarray) -> np.ndarray:
    return np.clip(np.round(x * 32767.0), -32768, 32767).astype(np.int16)


def rms_dbfs(samples: np.ndarray) -> float:
    if len(samples) == 0:
        return -120.0
    x = to_float(samples)
    r = float(np.sqrt(np.mean(x * x)))
    return 20 * np.log10(r) if r > 1e-9 else -120.0


def resample(x: np.ndarray, sr_in: int, sr_out: int = SAMPLE_RATE) -> np.ndarray:
    """Float resampling with a windowed sinc low pass before linear interpolation (no scipy)."""
    x = np.asarray(x, dtype=np.float32)
    if sr_in == sr_out or len(x) == 0:
        return x
    if sr_out < sr_in:
        cutoff = 0.5 * sr_out / sr_in * 0.95  # normalized to the input rate
        taps = 63
        n = np.arange(taps) - (taps - 1) / 2
        h = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(taps)
        h /= h.sum()
        x = np.convolve(x, h, mode="same").astype(np.float32)
    n_out = int(round(len(x) * sr_out / sr_in))
    t_out = np.arange(n_out) * (sr_in / sr_out)
    return np.interp(t_out, np.arange(len(x)), x).astype(np.float32)


def decode_wav_bytes(data: bytes) -> tuple[np.ndarray, int]:
    """Decode a PCM wav (8/16/32 bit int, any channels) to mono float32 and its rate."""
    with wave.open(io.BytesIO(data), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if width == 2:
        x = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        x = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        x = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128) / 128.0
    else:
        raise ValueError(f"unsupported wav sample width {width}")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def wav_to_pcm16(data: bytes) -> np.ndarray:
    """Any PCM wav bytes -> int16 mono 16 kHz."""
    x, sr = decode_wav_bytes(data)
    return to_int16(resample(x, sr, SAMPLE_RATE))


def write_wav(path: str | Path, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
    """Write mono (1D) or stereo (N x 2) int16 samples."""
    arr = np.asarray(samples, dtype=np.int16)
    channels = 1 if arr.ndim == 1 else arr.shape[1]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(arr.astype("<i2").tobytes())


def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    """Read an int16 wav. Returns (samples, rate); stereo comes back as N x 2."""
    with wave.open(str(path), "rb") as w:
        ch = w.getnchannels()
        sr = w.getframerate()
        if w.getsampwidth() != 2:
            raise ValueError("only 16 bit wav files are supported")
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.int16)
    if ch > 1:
        data = data.reshape(-1, ch)
    return data, sr


def trim_silence(samples: np.ndarray, threshold_dbfs: float = -45.0, win_ms: int = 10, keep_ms: int = 0) -> np.ndarray:
    """Remove leading and trailing quiet audio (energy based), so speech start and end are exact."""
    if len(samples) == 0:
        return samples
    win = max(1, ms_to_samples(win_ms))
    n = len(samples) // win
    if n == 0:
        return samples
    x = to_float(samples[: n * win]).reshape(n, win)
    db = 10 * np.log10(np.mean(x * x, axis=1) + 1e-12)
    loud = np.nonzero(db > threshold_dbfs)[0]
    if len(loud) == 0:
        return samples[:0]
    keep = ms_to_samples(keep_ms)
    start = max(0, loud[0] * win - keep)
    end = min(len(samples), (loud[-1] + 1) * win + keep)
    return samples[start:end]


def frame_is_loud(frame: np.ndarray, threshold_dbfs: float = -45.0) -> bool:
    return rms_dbfs(frame) > threshold_dbfs


def tone(ms: int, freq: float = 440.0, amp: float = 0.3, sr: int = SAMPLE_RATE) -> np.ndarray:
    """A test tone with 5 ms fades (used by tests and fakes)."""
    n = ms_to_samples(ms)
    t = np.arange(n) / sr
    x = amp * np.sin(2 * np.pi * freq * t)
    fade = min(n // 2, ms_to_samples(5))
    if fade:
        ramp = np.linspace(0, 1, fade)
        x[:fade] *= ramp
        x[-fade:] *= ramp[::-1]
    return to_int16(x)
