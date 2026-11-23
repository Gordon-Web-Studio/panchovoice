"""Command line entry point: `acme-agent`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time

import numpy as np


def _env_bug_default() -> bool:
    return os.environ.get("ACME_BUG", "1").strip().lower() not in ("0", "false", "no", "off")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="acme-agent", description="ACME Appointments demo voice agent (pv-ws/1)")
    p.add_argument("--host", default=os.environ.get("ACME_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("ACME_PORT", "8710")))
    p.add_argument("--model", default=os.environ.get("ACME_MODEL", "qwen2.5:7b"), help="Ollama model")
    p.add_argument("--stt-model", default=os.environ.get("ACME_STT_MODEL", "base"),
                   help="faster-whisper model (base is fast; small is more accurate but about 2x slower end to end)")
    p.add_argument("--voice", default=os.environ.get("ACME_VOICE", "af_heart"), help="Kokoro voice id")
    p.add_argument("--language", default=os.environ.get("ACME_LANGUAGE", "en"), help="default language if start has none")
    p.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    p.add_argument("--kokoro-url", default=os.environ.get("KOKORO_URL", "http://localhost:8880"))
    p.add_argument("--brain", choices=["ollama", "claude"], default=os.environ.get("ACME_BRAIN", "ollama"),
                   help="LLM: local Ollama, or the Claude API (needs ANTHROPIC_API_KEY)")
    p.add_argument("--claude-model", default=os.environ.get("ACME_CLAUDE_MODEL", "claude-haiku-5-5"),
                   help="Claude model for --brain claude")
    p.add_argument("--stt", choices=["whisper", "deepgram"], default=os.environ.get("ACME_STT", "whisper"),
                   help="speech to text: local faster-whisper, or Deepgram (needs DEEPGRAM_API_KEY)")
    p.add_argument("--tts", choices=["kokoro", "deepgram"], default=os.environ.get("ACME_TTS", "kokoro"),
                   help="text to speech: local Kokoro, or Deepgram Aura (needs DEEPGRAM_API_KEY)")
    p.add_argument("--deepgram-stt-model", default=os.environ.get("ACME_DEEPGRAM_STT_MODEL", "nova-3"))
    p.add_argument("--deepgram-voice", default=os.environ.get("ACME_DEEPGRAM_VOICE", "aura-2-thalia-en"),
                   help="Deepgram Aura voice, for example aura-2-thalia-en or aura-2-celeste-es")
    p.add_argument("--no-bug", dest="bug", action="store_false", default=_env_bug_default(),
                   help="turn off the planted bug (agent confirms day and time before booking)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)  # used by the anthropic SDK
    logging.getLogger("websockets").setLevel(logging.WARNING)
    log = logging.getLogger("acme_agent")

    from .call import Settings
    from .ears import DeepgramTranscriber, StreamingVad, Transcriber
    from .server import serve_forever

    dg_key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
    if "deepgram" in (args.stt, args.tts) and not dg_key:
        log.error("DEEPGRAM_API_KEY is not set. Set it in your shell to use --stt/--tts deepgram.")
        return 2

    if args.brain == "claude":
        import asyncio as _asyncio

        from .brain import BrainError
        from .claude_brain import ClaudeBrain

        log.info("brain: Claude API %s (checking the key...)", args.claude_model)
        try:
            import anthropic

            # own client: the server runs in a different event loop later
            _asyncio.run(ClaudeBrain(args.claude_model, client=anthropic.AsyncAnthropic()).warm_up())
        except BrainError as e:
            log.error("%s. Set ANTHROPIC_API_KEY in your shell (or run `ant auth login`).", e)
            return 2

    t = time.perf_counter()
    if args.stt == "deepgram":
        log.info("speech to text: Deepgram %s (checking the key...)", args.deepgram_stt_model)
        transcriber = DeepgramTranscriber(dg_key, args.deepgram_stt_model)
        try:
            transcriber.transcribe(np.zeros(16000, dtype=np.float32), args.language)  # checks the key
        except Exception as e:
            log.error("Deepgram STT check failed: %s", e)
            return 2
        transcriber.strict = False  # during calls, log errors instead of crashing the turn
    else:
        log.info("loading faster-whisper %s (cpu, int8)...", args.stt_model)
        transcriber = Transcriber(args.stt_model)
        transcriber.transcribe(np.zeros(16000, dtype=np.float32), args.language)  # warm up
    StreamingVad()  # load the VAD model once (local, free)
    if args.tts == "deepgram":
        log.info("text to speech: Deepgram %s", args.deepgram_voice)
    log.info("speech ready in %d ms", int((time.perf_counter() - t) * 1000))

    settings = Settings(
        ollama_url=args.ollama_url,
        kokoro_url=args.kokoro_url,
        model=args.model,
        voice=args.voice,
        language=args.language,
        bug=args.bug,
        brain=args.brain,
        claude_model=args.claude_model,
        tts=args.tts,
        deepgram_voice=args.deepgram_voice,
        deepgram_api_key=dg_key,
    )
    try:
        asyncio.run(serve_forever(args.host, args.port, settings, transcriber))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
