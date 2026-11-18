"""Unit tests for the parts that need no running services."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mock_tools import INLINE_FIXTURE, MockBackend, normalize_time  # noqa: E402

from acme_agent.brain import SentenceSplitter  # noqa: E402
from acme_agent.cli import build_parser  # noqa: E402
from acme_agent.ears import VAD_FRAME, Ears, Endpointer  # noqa: E402
from acme_agent.mouth import Resampler24to16, resample_24k_to_16k, split_sentences  # noqa: E402
from acme_agent.prompts import system_prompt  # noqa: E402
from acme_agent.tools import to_ollama_tools  # noqa: E402

# ---- prompt and the planted bug -------------------------------------------------------


def test_bug_prompt_skips_confirmation():
    p = system_prompt(bug=True)
    assert "Do not ask the caller to confirm" in p
    assert "Is that correct" not in p


def test_fixed_prompt_requires_confirmation():
    p = system_prompt(bug=False)
    assert "Is that correct" in p and "only after the caller clearly says yes" in p


def test_prompt_keeps_guardrails_in_both_modes():
    for bug in (True, False):
        p = system_prompt(bug)
        assert "Never reveal" in p
        assert "Never give data of a different person" in p
        assert "Never invent" in p


def test_prompt_forbids_invented_caller_names_in_both_modes():
    for bug in (True, False):
        for lang in ("en", "es"):
            assert "Never use a name the caller did not say" in system_prompt(bug, lang)


def test_cli_bug_flag_and_env(monkeypatch):
    monkeypatch.delenv("ACME_BUG", raising=False)
    assert build_parser().parse_args([]).bug is True
    assert build_parser().parse_args(["--no-bug"]).bug is False
    monkeypatch.setenv("ACME_BUG", "0")
    assert build_parser().parse_args([]).bug is False
    a = build_parser().parse_args([])
    assert (a.port, a.model, a.stt_model, a.voice, a.language) == (8710, "qwen2.5:7b", "base", "af_heart", "en")


# ---- sentence splitting ---------------------------------------------------------------


def test_split_sentences_merges_short_pieces():
    assert split_sentences("Sure. Tuesday at 10 AM is free. What is your name?") == [
        "Sure. Tuesday at 10 AM is free.",
        "What is your name?",
    ]


def test_streaming_splitter_waits_for_whitespace_and_skips_abbreviations():
    s = SentenceSplitter()
    out = []
    for piece in ["I booked you for 10 a.m. on", " Tuesday. See", " you then!"]:
        out += s.feed(piece)
    out += s.flush()
    assert out == ["I booked you for 10 a.m. on Tuesday.", "See you then!"]


def test_streaming_splitter_splits_after_am_before_a_new_sentence():
    s = SentenceSplitter()
    out = s.feed("Tuesday at 10 a.m.")  # not yet known if the sentence ended
    assert out == []
    out += s.feed(" Could you tell me your name?")
    out += s.flush()
    assert out == ["Tuesday at 10 a.m.", "Could you tell me your name?"]


# ---- resampling -----------------------------------------------------------------------


def test_resampler_length_and_chunk_independence():
    x = (np.sin(np.arange(24000) / 24000 * 2 * np.pi * 440) * 10000).astype("<i2").tobytes()
    whole = resample_24k_to_16k(x)
    assert abs(len(whole) // 2 - 16000) <= 1
    r = Resampler24to16()
    parts = b"".join(r.feed(x[i : i + 777]) for i in range(0, len(x), 777)) + r.flush()
    assert parts == whole


def test_resampler_keeps_frequency():
    t = np.arange(24000) / 24000
    x = (np.sin(2 * np.pi * 300 * t) * 10000).astype("<i2").tobytes()
    y = np.frombuffer(resample_24k_to_16k(x), dtype="<i2").astype(np.float32)
    spectrum = np.abs(np.fft.rfft(y))
    assert abs(np.argmax(spectrum) * 16000 / len(y) - 300) < 3


# ---- tools ----------------------------------------------------------------------------


def test_to_ollama_tools():
    specs = [{k: t[k] for k in ("name", "description", "params")} for t in INLINE_FIXTURE["tools"]]
    tools = to_ollama_tools(specs)
    assert [t["function"]["name"] for t in tools] == ["find_slot", "book_appointment", "check_balance", "cancel_appointment"]
    fs = tools[0]["function"]["parameters"]
    assert fs["required"] == ["day", "time"] and fs["properties"]["day"]["type"] == "string"


def test_mock_backend_contract_semantics():
    b = MockBackend(json.loads(json.dumps(INLINE_FIXTURE)))
    assert b.call("find_slot", {"day": "Tuesday", "time": "10 am"}) == {"ok": True, "result": {"day": "tuesday", "time": "10:00"}}
    assert b.call("find_slot", {"day": "friday", "time": "10"}) == {"ok": False, "error": "not_found"}
    assert b.call("book_appointment", {"name": "Sam Rivera", "day": "tuesday", "time": "10:00"}) == {"ok": True}
    assert b.call("book_appointment", {"name": "Jordan Lee", "day": "tuesday", "time": "10:00"}) == {"ok": False, "error": "slot_not_available"}
    assert b.call("check_balance", {"name": "sam rivera"}) == {"ok": True, "result": {"name": "Sam Rivera", "balance": 120.5}}
    assert b.call("cancel_appointment", {"name": "Sam Rivera", "day": "Tuesday"}) == {"ok": True, "deleted": 1}
    assert len(b.calls) == 6
    assert normalize_time("2 pm") == "14:00" and normalize_time("10:30") == "10:30"


# ---- endpointing and barge-in thresholds ----------------------------------------------


def _feed_probs(ep: Endpointer, probs):
    frame = np.zeros(VAD_FRAME, dtype=np.float32)
    return [(i, ev.kind, ev.voiced_ms) for i, p in enumerate(probs) if (ev := ep.process(frame, p))]


def test_endpointer_turn_ends_after_700ms_silence():
    ep = Endpointer()
    probs = [0.0] * 5 + [0.9] * 30 + [0.0] * 30
    events = _feed_probs(ep, probs)
    assert events[0][1] == "start"
    kind, idx = events[1][1], events[1][0]
    assert kind == "end"
    # 700 ms of 32 ms frames = 22 frames of silence after the last speech frame (index 34)
    assert idx == 34 + 22


def test_endpointer_short_pause_does_not_end_turn():
    ep = Endpointer()
    probs = [0.9] * 20 + [0.0] * 15 + [0.9] * 20 + [0.0] * 30  # 480 ms pause inside speech
    kinds = [k for _, k, _ in _feed_probs(ep, probs)]
    assert kinds == ["start", "end"]


def test_endpointer_epoch_changes_when_speech_resumes():
    """Speculative STT is reused only if the epoch did not change; a resumed pause bumps it."""
    ep = Endpointer()
    _feed_probs(ep, [0.9] * 10 + [0.0] * 12)  # in speech, 384 ms silence
    assert ep.in_speech and ep.silence_ms >= 320
    before = ep.epoch
    _feed_probs(ep, [0.9] * 5)
    assert ep.epoch == before + 1 and ep.silence_ms == 0
    snap = ep.snapshot()
    assert len(snap) == ep._frames.__len__() * VAD_FRAME


def test_endpointer_drops_blips():
    ep = Endpointer()
    probs = [0.9] * 5 + [0.0] * 30  # 160 ms of speech
    kinds = [k for _, k, _ in _feed_probs(ep, probs)]
    assert kinds == ["start", "blip"]


def test_ears_buffers_partial_frames_and_tracks_voiced_ms():
    probs = iter([0.9] * 100)
    ears = Ears(vad=lambda frame: next(probs))
    pcm = b"\x00\x01" * 9600  # 600 ms
    events = []
    for i in range(0, len(pcm), 640):  # 20 ms caller frames, VAD runs on 32 ms frames
        events += ears.feed(pcm[i : i + 640])
    assert [e.kind for e in events] == ["start"]
    assert ears.endpointer.in_speech and ears.endpointer.voiced_ms >= 300  # enough for barge-in


# ---- protocol smoke test (no LLM, no TTS needed) --------------------------------------


class _FakeTranscriber:
    def transcribe(self, audio, language="en"):
        return ""


def test_server_ready_health_and_second_connection():
    import socket

    import websockets

    from acme_agent.call import Settings
    from acme_agent.server import serve_forever

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    settings = Settings(ollama_url="http://127.0.0.1:9", kokoro_url="http://127.0.0.1:9")

    async def scenario():
        server = asyncio.create_task(serve_forever("127.0.0.1", port, settings, _FakeTranscriber()))
        await asyncio.sleep(0.5)
        try:
            for _ in range(2):
                async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as ws:
                    await ws.send(json.dumps({"type": "start", "protocol": "pv-ws/1", "session": "x",
                                              "language": "en", "mock_tools_url": "http://127.0.0.1:9/mock/x",
                                              "sample_rate": 16000}))
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    assert msg == {"type": "ready"}
                    await ws.send(b"\x00" * 640)
                    await ws.send(json.dumps({"type": "hangup"}))
            import httpx

            async with httpx.AsyncClient() as c:
                r = await c.get(f"http://127.0.0.1:{port}/health")
                assert r.status_code == 200
        finally:
            server.cancel()
            with pytest.raises(BaseException):
                await server

    asyncio.run(scenario())
