"""A tiny http-turn agent for tests and demos (FastAPI + uvicorn in a thread, free port).

- POST /transcribe: multipart file `audio` or JSON {"audio": base64}. Returns {"transcript": ...}
  from the `heard` list (turn by turn per session), or "audio <n> ms" when the list is empty.
- POST /chat: {"messages": [{role, content}]}. Rule based replies from the whole history (day, time,
  name). Returns {"reply": ..., "actions": [{"type": tool, "args": {...}}]}. With `use_mock`, it also
  calls the tool on the mock server named in the X-PV-Mock-Tools-Url header.
- POST /speak: {"text": ...}. Returns a 24 kHz wav tone (50 ms per character, 400 ms minimum),
  or the mp3 test tone with `speak_format="mp3"`.
- GET /health: ok.

Run it alone for a demo: `uv run python tests/fake_http_agent.py --port 9000`.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

import base64
import io
import re
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import numpy as np

MP3_TONE = Path(__file__).parent / "data" / "tone.mp3"

_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]
_HOURS = {"nine": 9, "ten": 10, "eleven": 11, "two": 14, "three": 15, "four": 16}


@dataclass
class FakeHttpAgentConfig:
    heard: list[str] = field(default_factory=list)  # transcribe answers, turn by turn
    stt_delay_ms: int = 100
    llm_delay_ms: int = 200
    tts_delay_ms: int = 100
    speak_format: str = "wav"  # wav | mp3
    use_mock: bool = False  # call tools on the mock server too
    require_header: tuple[str, str] | None = None  # (name, value): else 401
    fail_chat_turns: set[int] = field(default_factory=set)  # these chat turns answer 500


def _tone_wav(ms: int, sr: int = 24000, freq: float = 330.0) -> bytes:
    n = int(sr * ms / 1000)
    t = np.arange(n) / sr
    x = (0.3 * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())
    return buf.getvalue()


def _find_day(text: str) -> str | None:
    return next((d for d in _DAYS if d in text), None)


def _find_time(text: str) -> str | None:
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?", text)
    if m:
        h = int(m.group(1))
        if m.group(3) and m.group(3).startswith("p") and h < 12:
            h += 12
        return f"{h:02d}:{m.group(2) or '00'}"
    for word, h in _HOURS.items():
        if re.search(rf"\b{word}\b", text):
            return f"{h:02d}:00"
    return None


def _find_name(text: str) -> str | None:
    m = re.search(r"(?i:name is|i am|this is)\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)", text)
    if m:
        return m.group(1)
    m = re.fullmatch(r"\s*([A-Z][a-z]+\s[A-Z][a-z]+)\.?\s*", text)
    return m.group(1) if m else None


def reply_for(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Rule based brain: look at the whole history, act on the last user line."""
    users = [str(m.get("content") or "") for m in messages if m.get("role") == "user"]
    last = users[-1] if users else ""
    low = last.lower()
    day = next((d for d in (_find_day(u.lower()) for u in reversed(users)) if d), None)
    hour = next((t for t in (_find_time(u.lower()) for u in reversed(users)) if t), None)
    name = _find_name(last)
    if "bye" in low:
        return "Goodbye, have a nice day.", []
    if name and day and hour:
        return (f"You are booked, {name.split()[0]}, on {day.title()} at {hour}.",
                [{"type": "book_appointment", "args": {"name": name, "day": day, "time": hour}}])
    if day and hour:
        return f"{day.title()} at {hour} is free. What is your name?", [{"type": "find_slot", "args": {"day": day, "time": hour}}]
    return "Hello, I can book an appointment for you. Which day and time?", []


class FakeHttpAgent:
    def __init__(self, cfg: FakeHttpAgentConfig | None = None):
        self.cfg = cfg or FakeHttpAgentConfig()
        self.port = 0
        self.requests: list[dict[str, Any]] = []  # {path, headers, body}
        self._turn: dict[str, int] = {}
        self._chat_turn: dict[str, int] = {}
        self._server = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def app(self):

        app = FastAPI(title="fake http-turn agent")
        cfg = self.cfg

        def gate(req: Request, path: str, body: Any) -> str:
            self.requests.append({"path": path, "headers": dict(req.headers), "body": body})
            if cfg.require_header:
                name, value = cfg.require_header
                if req.headers.get(name) != value:
                    raise HTTPException(401, "no auth")
            return req.headers.get("x-pv-session", "default")

        @app.get("/health")
        def health():
            return PlainTextResponse("ok")

        @app.post("/transcribe")
        async def transcribe(req: Request):
            ctype = req.headers.get("content-type", "")
            if ctype.startswith("multipart/"):
                form = await req.form()
                audio = await form["audio"].read()
                body: Any = {"audio_bytes": len(audio), "form": {k: v for k, v in form.items() if k != "audio"}}
            else:
                data = await req.json()
                audio = base64.b64decode(data["audio"])
                body = {"audio_bytes": len(audio), "json_keys": sorted(data)}
            sid = gate(req, "/transcribe", body)
            time.sleep(cfg.stt_delay_ms / 1000)
            i = self._turn.get(sid, 0)
            self._turn[sid] = i + 1
            with wave.open(io.BytesIO(audio), "rb") as w:
                ms = int(w.getnframes() * 1000 / w.getframerate())
            text = cfg.heard[i] if i < len(cfg.heard) else f"audio {ms} ms"
            return {"transcript": text}

        @app.post("/chat")
        async def chat(req: Request):
            data = await req.json()
            sid = gate(req, "/chat", data)
            i = self._chat_turn.get(sid, 0)
            self._chat_turn[sid] = i + 1
            time.sleep(cfg.llm_delay_ms / 1000)
            if i in cfg.fail_chat_turns:
                return JSONResponse({"error": "boom"}, status_code=500)
            reply, actions = reply_for(data.get("messages") or [])
            mock = req.headers.get("x-pv-mock-tools-url")
            if cfg.use_mock and mock:
                for a in actions:
                    httpx.post(f"{mock}/tools/{a['type']}", json=a["args"], timeout=5)
            return {"reply": reply, "actions": actions}

        @app.post("/speak")
        async def speak(req: Request):
            data = await req.json()
            gate(req, "/speak", data)
            time.sleep(cfg.tts_delay_ms / 1000)
            if cfg.speak_format == "mp3":
                return Response(MP3_TONE.read_bytes(), media_type="audio/mpeg")
            ms = max(400, len(str(data.get("text") or "")) * 50)
            return Response(_tone_wav(ms), media_type="audio/wav")

        return app

    def start(self, port: int = 0) -> "FakeHttpAgent":
        import socket

        import uvicorn

        if not port:
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
        self.port = port
        self._server = uvicorn.Server(uvicorn.Config(self.app(), host="127.0.0.1", port=port, log_level="warning",
                                                     lifespan="off"))
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise RuntimeError("fake http agent did not start")
            time.sleep(0.02)
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="fake http-turn agent")
    p.add_argument("--port", type=int, default=9000)
    p.add_argument("--mp3", action="store_true")
    p.add_argument("--mock", action="store_true", help="also call tools on the mock server")
    args = p.parse_args()
    agent = FakeHttpAgent(FakeHttpAgentConfig(speak_format="mp3" if args.mp3 else "wav", use_mock=args.mock)).start(args.port)
    print(f"fake http-turn agent on {agent.url} (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        agent.stop()
