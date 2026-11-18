from __future__ import annotations

import numpy as np
import pytest

from fake_agent import two_channel
from panchovoice.audio.noise import add_noise
from panchovoice.audio.pcm import resample, rms_dbfs, tone, trim_silence
from panchovoice.metrics import TurnTiming, latency_summary, measure_turns, normalize_text, percentile, wer


def test_latency_from_synthetic_two_channel_audio():
    # caller speaks 1000-2500 and 6000-7000; agent answers at 3200 and 7450
    caller, agent = two_channel([(1000, 2500), (6000, 7000)], [(3200, 5000), (7450, 8800)], 10000)
    turns = [TurnTiming(1000, 2500, 6000), TurnTiming(6000, 7000, None)]
    m = measure_turns(agent, turns, vad="energy")
    assert [x.agent_start_ms for x in m] == [3200, 7450]
    assert [x.latency_ms for x in m] == [700, 450]
    assert m[0].agent_end_ms == 5000 and m[1].agent_end_ms == 8800
    s = latency_summary(x.latency_ms for x in m)
    assert s == {"latency_p50_ms": 450, "latency_p95_ms": 700, "latency_max_ms": 700}


def test_no_reply_gives_none_and_onset_must_be_before_next_turn():
    caller, agent = two_channel([(1000, 2000), (4000, 5000)], [(5600, 6500)], 8000)
    m = measure_turns(agent, [TurnTiming(1000, 2000, 4000), TurnTiming(4000, 5000, None)], vad="energy")
    assert m[0].latency_ms is None and m[0].agent_start_ms is None
    assert m[1].latency_ms == 600


def test_barge_in_stop_ms():
    # agent talks 3000-6000 but stops at 4700 because the caller started at 4200
    caller, agent = two_channel([(1000, 2000), (4200, 5200)], [(3000, 4700), (5900, 7000)], 8000)
    m = measure_turns(agent, [TurnTiming(1000, 2000, 4200), TurnTiming(4200, 5200, None, interrupted=True)], vad="energy")
    assert m[1].stop_ms == 500
    assert m[1].latency_ms == 700


def test_percentile_nearest_rank():
    vals = [15, 20, 35, 40, 50]
    assert percentile(vals, 50) == 35
    assert percentile(vals, 95) == 50
    assert percentile(vals, 30) == 20
    assert percentile([7], 95) == 7
    assert percentile([], 50) is None
    assert percentile(list(range(1, 101)), 95) == 95


@pytest.mark.parametrize("raw,out", [
    ("Hi, I need an appointment on Tuesday at 10 AM.", "hi i need an appointment on tuesday at 10 am"),
    ("at ten a.m.", "at 10 am"),
    ("Tuesday at 10 a.m.", "tuesday at 10 am"),
    ("3:30 P.M.", "3 30 pm"),
    ("Twenty-one", "21"),
    ("at 10:00", "at 10"),
    ("It's Sam's!", "its sams"),
    ("Hola, a las DIEZ de la mañana", "hola a las 10 de la manana"),
])
def test_normalize_text(raw, out):
    assert normalize_text(raw) == out


def test_wer_values():
    assert wer(["Hi, I need an appointment on Tuesday at 10 AM."], ["hi i need an appointment on tuesday at ten am"]) == 0.0
    assert wer(["one two three four"], ["one two three"]) == 0.25
    assert wer(["one two three four"], [""]) == 1.0
    assert wer([""], ["anything"]) is None
    # corpus WER: errors summed over all words
    assert wer(["a b", "c d e f"], ["a b", "c x e f"]) == pytest.approx(1 / 6, abs=1e-4)


def test_trim_and_resample_and_noise():
    x = np.concatenate([np.zeros(8000, np.int16), tone(500), np.zeros(4000, np.int16)])
    t = trim_silence(x)
    assert abs(len(t) - 8000) < 400
    y = resample(np.ones(2400, np.float32), 24000, 16000)
    assert len(y) == 1600
    noisy = add_noise(tone(1000), "white", 10)
    assert abs(rms_dbfs(noisy) - rms_dbfs(tone(1000))) < 1.5
    for kind in ("street", "cafe"):
        assert len(add_noise(tone(300), kind, 10)) == len(tone(300))


@pytest.mark.live
def test_silero_onset_on_real_speech(tmp_path):
    from panchovoice.audio.tts import KokoroTTS
    from panchovoice.audio.vad import silero_segments

    speech = KokoroTTS("http://localhost:8880", tmp_path).synth("Sure, Tuesday at ten is free.", "af_heart")
    rec = np.concatenate([np.zeros(16000, np.int16), speech, np.zeros(8000, np.int16)])
    segs = silero_segments(rec)
    assert segs and abs(segs[0][0] / 16 - 1000) < 80
