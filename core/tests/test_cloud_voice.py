"""Cloud voice providers: ElevenLabs and Deepgram TTS, Deepgram STT. No real network: httpx MockTransport."""

from __future__ import annotations

import io
import json
import wave

import httpx
import numpy as np
import pytest

from panchovoice import doctor, wizard
from panchovoice.audio.pcm import tone, to_bytes
from panchovoice.audio.stt import DeepgramSTT, STTError, make_transcriber
from panchovoice.audio.tts import DeepgramTTS, ElevenLabsTTS, KokoroTTS, TTSError, make_tts
from panchovoice.config import STTConfig, TTSConfig, parse_config_text
from panchovoice.errors import ConfigError

SPEECH = tone(500)  # int16 16 kHz, loud enough to survive trim_silence


def _client(handler, seen: list[httpx.Request]) -> httpx.Client:
    def wrap(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)
    return httpx.Client(transport=httpx.MockTransport(wrap))


def _pcm_ok(_req: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=to_bytes(SPEECH))


# ----------------------------------------------------------------------------- config


def test_tts_config_defaults_per_provider():
    k = TTSConfig()
    assert (k.provider, k.url, k.is_cloud) == ("kokoro", "http://localhost:8880", False)
    e = TTSConfig(provider="elevenlabs")
    assert e.url == "https://api.elevenlabs.io"
    assert e.model == "eleven_multilingual_v2"
    assert e.default_voice and e.key_env == "ELEVENLABS_API_KEY" and e.is_cloud
    d = TTSConfig(provider="deepgram")
    assert (d.url, d.default_voice, d.key_env) == ("https://api.deepgram.com", "aura-2-thalia-en", "DEEPGRAM_API_KEY")


def test_stt_config_defaults_and_model_rules():
    assert STTConfig().model == "small"
    d = STTConfig(provider="deepgram")
    assert (d.model, d.key_env, d.is_cloud) == ("nova-3", "DEEPGRAM_API_KEY", True)
    with pytest.raises(ValueError, match="faster-whisper model"):
        STTConfig(model="nova-3")


def test_api_key_env_must_be_a_name_not_a_key():
    with pytest.raises(ValueError, match="env var name"):
        TTSConfig(provider="elevenlabs", api_key_env="sk-123 secret")


def test_yaml_with_cloud_providers_and_voice_map():
    cfg = parse_config_text(
        "version: 1\n"
        "tts: {provider: elevenlabs, voices: {af_heart: VOICE_A}, api_key_env: MY_EL_KEY}\n"
        "stt: {provider: deepgram, model: nova-2}\n"
    )
    assert cfg.tts.voices == {"af_heart": "VOICE_A"} and cfg.tts.key_env == "MY_EL_KEY"
    assert cfg.stt.model == "nova-2"
    with pytest.raises(ConfigError):
        parse_config_text("version: 1\ntts: {provider: elevenlabs, api_key: oops}\n")


# ----------------------------------------------------------------------------- ElevenLabs


def test_elevenlabs_request_and_cache(tmp_path):
    seen: list[httpx.Request] = []
    tts = ElevenLabsTTS("k-el", "https://el.test", "eleven_multilingual_v2", "DEFAULTV", {"em_alex": "ALEXV"},
                        tmp_path, client=_client(_pcm_ok, seen))
    out = tts.synth("Hello there.", "af_heart")
    assert out.dtype == np.int16 and len(out) > 0
    req = seen[0]
    assert req.url.path == "/v1/text-to-speech/DEFAULTV"  # Kokoro id -> default_voice
    assert req.url.params["output_format"] == "pcm_16000"
    assert req.headers["xi-api-key"] == "k-el"
    body = json.loads(req.content)
    assert body == {"text": "Hello there.", "model_id": "eleven_multilingual_v2"}

    tts.synth("Hello there.", "af_heart")
    assert len(seen) == 1  # cached

    tts.synth("Hi.", "em_alex", speed=2.0)
    assert seen[-1].url.path == "/v1/text-to-speech/ALEXV"  # mapped
    assert json.loads(seen[-1].content)["voice_settings"] == {"speed": 1.2}  # clamped
    tts.synth("Hi.", "RawVoiceId123")
    assert seen[-1].url.path == "/v1/text-to-speech/RawVoiceId123"  # provider id passes through


def test_cache_key_differs_by_provider_and_voice(tmp_path):
    a = ElevenLabsTTS("k", "https://x", "m", "V1", cache_dir=tmp_path)
    b = ElevenLabsTTS("k", "https://x", "m", "V2", cache_dir=tmp_path)
    c = DeepgramTTS("k", "https://x", "", "V1", cache_dir=tmp_path)
    keys = {t._cache_key("hi", "af_heart", 1.0) for t in (a, b, c)}
    assert len(keys) == 3
    assert KokoroTTS.cache_key("hi", "af_heart", 1.0) not in keys


def test_elevenlabs_bad_key_is_a_clear_error():
    seen: list[httpx.Request] = []
    tts = ElevenLabsTTS("bad", "https://el.test", "m", "V", client=_client(lambda r: httpx.Response(401, text="nope"), seen))
    with pytest.raises(TTSError, match="rejected the API key"):
        tts.synth("Hello.", "af_heart")


# ----------------------------------------------------------------------------- Deepgram TTS


def test_deepgram_tts_request():
    seen: list[httpx.Request] = []
    tts = DeepgramTTS("k-dg", "https://dg.test", "", "aura-2-thalia-en", client=_client(_pcm_ok, seen))
    assert len(tts.synth("Book me for Monday.", "af_heart")) > 0
    req = seen[0]
    assert req.url.path == "/v1/speak"
    p = req.url.params
    assert (p["model"], p["encoding"], p["sample_rate"], p["container"]) == ("aura-2-thalia-en", "linear16", "16000", "none")
    assert req.headers["authorization"] == "Token k-dg"
    assert json.loads(req.content) == {"text": "Book me for Monday."}


def test_silence_from_provider_is_an_error():
    seen: list[httpx.Request] = []
    silent = lambda r: httpx.Response(200, content=to_bytes(np.zeros(8000, dtype=np.int16)))  # noqa: E731
    tts = DeepgramTTS("k", "https://dg.test", "", "v", client=_client(silent, seen))
    with pytest.raises(TTSError, match="silence"):
        tts.synth("Hello.", "v")


# ----------------------------------------------------------------------------- Deepgram STT


def _dg_answer(text: str) -> httpx.Response:
    return httpx.Response(200, json={"results": {"channels": [{"alternatives": [{"transcript": text}]}]}})


def test_deepgram_stt_request_and_parse():
    seen: list[httpx.Request] = []
    stt = DeepgramSTT("k-dg", "nova-3", "https://dg.test", client=_client(lambda r: _dg_answer(" Tuesday at ten. "), seen))
    assert stt(SPEECH, "es") == "Tuesday at ten."
    req = seen[0]
    assert req.url.path == "/v1/listen"
    assert (req.url.params["model"], req.url.params["language"]) == ("nova-3", "es")
    assert req.headers["content-type"] == "audio/wav"
    assert req.headers["authorization"] == "Token k-dg"
    with wave.open(io.BytesIO(req.content)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (1, 16000, len(SPEECH))


def test_deepgram_stt_short_audio_skips_the_request():
    seen: list[httpx.Request] = []
    stt = DeepgramSTT("k", client=_client(lambda r: _dg_answer("x"), seen))
    assert stt(np.zeros(100, dtype=np.int16), "en") == "" and not seen


def test_deepgram_stt_errors():
    seen: list[httpx.Request] = []
    with pytest.raises(STTError, match="rejected"):
        DeepgramSTT("k", client=_client(lambda r: httpx.Response(403), seen))(SPEECH, "en")
    with pytest.raises(STTError, match="unexpected"):
        DeepgramSTT("k", client=_client(lambda r: httpx.Response(200, json={"x": 1}), seen))(SPEECH, "en")


# ----------------------------------------------------------------------------- factories, doctor, wizard


def test_factories_need_the_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    with pytest.raises(TTSError, match="ELEVENLABS_API_KEY is not set"):
        make_tts(TTSConfig(provider="elevenlabs"), tmp_path)
    with pytest.raises(STTError, match="DEEPGRAM_API_KEY is not set"):
        make_transcriber(STTConfig(provider="deepgram"))
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k1")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k2")
    assert isinstance(make_tts(TTSConfig(provider="elevenlabs"), tmp_path), ElevenLabsTTS)
    assert isinstance(make_tts(TTSConfig(provider="deepgram"), tmp_path), DeepgramTTS)
    assert isinstance(make_tts(TTSConfig(), tmp_path), KokoroTTS)
    assert isinstance(make_transcriber(STTConfig(provider="deepgram")), DeepgramSTT)


def test_doctor_cloud_checks(monkeypatch):
    cfg = parse_config_text("version: 1\ntts: {provider: elevenlabs}\nstt: {provider: deepgram}\n")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    t, s = doctor.check_tts(cfg), doctor.check_stt(cfg)
    assert (t.status, s.status) == ("FAIL", "FAIL")
    assert "ELEVENLABS_API_KEY" in t.fix and "DEEPGRAM_API_KEY" in s.fix
    monkeypatch.setenv("ELEVENLABS_API_KEY", "x")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "y")
    assert (doctor.check_tts(cfg).status, doctor.check_stt(cfg).status) == ("PASS", "PASS")


@pytest.mark.parametrize("stack", list(wizard.VOICE_STACKS))
def test_wizard_config_for_each_voice_stack(stack):
    tts_p, stt_p = wizard.VOICE_STACKS[stack]
    text = wizard.render_config("claude-cli", "haiku", [dict(wizard.DEMO_AGENT)], None, tts_p, stt_p)
    cfg = parse_config_text(text)
    assert (cfg.tts.provider, cfg.stt.provider) == (tts_p, stt_p)
    assert "API_KEY:" not in text  # never a key in the yaml
