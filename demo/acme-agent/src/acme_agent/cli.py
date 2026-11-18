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
    p.add_argument("--no-bug", dest="bug", action="store_false", default=_env_bug_default(),
                   help="turn off the planted bug (agent confirms day and time before booking)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    log = logging.getLogger("acme_agent")

    from .call import Settings
    from .ears import StreamingVad, Transcriber
    from .server import serve_forever

    t = time.perf_counter()
    log.info("loading faster-whisper %s (cpu, int8)...", args.stt_model)
    transcriber = Transcriber(args.stt_model)
    transcriber.transcribe(np.zeros(16000, dtype=np.float32), args.language)  # warm up
    StreamingVad()  # load the VAD model once
    log.info("speech models ready in %d ms", int((time.perf_counter() - t) * 1000))

    settings = Settings(
        ollama_url=args.ollama_url,
        kokoro_url=args.kokoro_url,
        model=args.model,
        voice=args.voice,
        language=args.language,
        bug=args.bug,
    )
    try:
        asyncio.run(serve_forever(args.host, args.port, settings, transcriber))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
