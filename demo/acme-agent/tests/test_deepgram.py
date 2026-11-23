"""Deepgram ears and mouth of the demo agent, against a fake Deepgram (httpx MockTransport)."""

from __future__ import annotations

import asyncio

import httpx
import numpy as np
import pytest

from acme_agent.call import Settings
from acme_agent.ears import DeepgramTranscriber
from acme_agent.mouth import DeepgramMouth, Mouth, make_mouth


def _stt_transport(seen: list, status: int = 200):
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if status != 200:
            return httpx.Response(status, json={"err_msg": "nope"})
        return httpx.Response(200, json={"results": {"channels": [{"alternatives": [{"transcript": " Tuesday at ten. "}]}]}})
    return httpx.MockTransport(handler)


def test_deepgram_transcriber_posts_wav_and_parses():
    seen: list = []
    t = DeepgramTranscriber("k", client=httpx.Client(transport=_stt_transport(seen)))
    assert t.transcribe(np.full(8000, 0.1, dtype=np.float32), "es") == "Tuesday at ten."
    req = seen[0]
    assert req.url.path == "/v1/listen" and req.url.params["model"] == "nova-3" and req.url.params["language"] == "es"
    assert req.headers["Authorization"] == "Token k" and req.content[:4] == b"RIFF"
    assert t.transcribe(np.zeros(100, dtype=np.float32)) == ""  # too short, not sent
    assert len(seen) == 1


def test_deepgram_transcriber_bad_key():
    t = DeepgramTranscriber("bad", client=httpx.Client(transport=_stt_transport([], 401)))
    with pytest.raises(RuntimeError, match="rejected DEEPGRAM_API_KEY"):
        t.transcribe(np.zeros(16000, dtype=np.float32))
    t.strict = False
    assert t.transcribe(np.zeros(16000, dtype=np.float32)) == ""


def test_deepgram_mouth_streams_whole_samples():
    seen: list = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, content=b"\x01" * 641)  # odd length: last byte must be held back

    async def go() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            m = DeepgramMouth(c, "k", "aura-2-celeste-es")
            return b"".join([chunk async for chunk in m.stream("Hola.")])

    out = asyncio.run(go())
    assert len(out) == 640
    p = seen[0].url.params
    assert (p["model"], p["encoding"], p["sample_rate"], p["container"]) == ("aura-2-celeste-es", "linear16", "16000", "none")
    assert seen[0].headers["Authorization"] == "Token k"


def test_deepgram_mouth_error_is_an_http_error():
    async def go() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={}))) as c:
            async for _ in DeepgramMouth(c, "bad").stream("hi"):
                pass

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(go())


def test_make_mouth_picks_provider():
    assert isinstance(make_mouth(None, Settings()), Mouth)
    m = make_mouth(None, Settings(tts="deepgram", deepgram_api_key="k", deepgram_voice="aura-2-thalia-en"))
    assert isinstance(m, DeepgramMouth) and m.voice == "aura-2-thalia-en"
    assert "deepgram_api_key" not in repr(Settings(deepgram_api_key="k"))  # never logged
