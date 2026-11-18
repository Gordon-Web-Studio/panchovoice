"""Simple line degradations: white, street and cafe noise mixed at a target SNR."""

from __future__ import annotations

import numpy as np

from .pcm import SAMPLE_RATE, to_float, to_int16


def _white(n: int, rng: np.random.Generator) -> np.ndarray:
    return rng.standard_normal(n).astype(np.float32)


def _pink(n: int, rng: np.random.Generator) -> np.ndarray:
    # spectral shaping of white noise by 1/sqrt(f)
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.arange(len(spec))
    f[0] = 1
    spec /= np.sqrt(f)
    return np.fft.irfft(spec, n).astype(np.float32)


def _brown(n: int, rng: np.random.Generator) -> np.ndarray:
    x = np.cumsum(rng.standard_normal(n)).astype(np.float32)
    # remove drift with a slow moving average
    k = SAMPLE_RATE // 10
    if n > k:
        x -= np.convolve(x, np.ones(k) / k, mode="same").astype(np.float32)
    return x


def _street(n: int, rng: np.random.Generator) -> np.ndarray:
    # low rumble (traffic) plus a few louder passing bursts
    x = _brown(n, rng)
    x /= np.std(x) + 1e-9
    env = np.ones(n, dtype=np.float32)
    for _ in range(max(1, n // (SAMPLE_RATE * 2))):
        c = rng.integers(0, n)
        w = int(SAMPLE_RATE * rng.uniform(0.3, 1.0))
        lo, hi = max(0, c - w), min(n, c + w)
        env[lo:hi] += np.hanning(hi - lo).astype(np.float32) * rng.uniform(0.5, 1.5)
    return x * env + 0.2 * _white(n, rng)


def _cafe(n: int, rng: np.random.Generator) -> np.ndarray:
    # pink noise with a slow, syllable like amplitude modulation (babble like)
    x = _pink(n, rng)
    x /= np.std(x) + 1e-9
    t = np.arange(n) / SAMPLE_RATE
    mod = 0.6 + 0.4 * np.abs(np.sin(2 * np.pi * 3.5 * t + rng.uniform(0, 6)) * np.sin(2 * np.pi * 0.7 * t))
    return (x * mod).astype(np.float32)


GENERATORS = {"white": _white, "street": _street, "cafe": _cafe}


def add_noise(samples: np.ndarray, kind: str, snr_db: float, seed: int = 7) -> np.ndarray:
    """Mix noise into int16 speech so that speech RMS / noise RMS equals snr_db."""
    if kind in (None, "", "none") or len(samples) == 0:
        return samples
    if kind not in GENERATORS:
        raise ValueError(f"unknown noise kind {kind!r}")
    rng = np.random.default_rng(seed)
    x = to_float(samples)
    sig_rms = float(np.sqrt(np.mean(x * x))) or 1e-4
    noise = GENERATORS[kind](len(x), rng)
    noise_rms = float(np.sqrt(np.mean(noise * noise))) or 1e-9
    noise *= (sig_rms / (10 ** (snr_db / 20))) / noise_rms
    return to_int16(x + noise)
