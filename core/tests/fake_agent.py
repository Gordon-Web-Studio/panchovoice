"""A tiny pv-ws/1 agent for tests: replies with a tone after the caller stops talking.

Per caller turn it can: send `transcript role=user` (from `heard`), call a mock tool over HTTP,
send `transcript role=agent`, then the reply audio (all at once, faster than real time).
When the caller talks while the reply is still playing, it sends `clear` after `stop_delay_ms`.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import numpy as np

from panchovoice.audio.pcm import SAMPLE_RATE, from_bytes, rms_dbfs, tone


@dataclass
class FakeAgentConfig:
    reply_delay_ms: int = 400          # caller silence before the agent starts its reply audio
    reply_ms: int = 900                # length of each reply tone
    end_silence_ms: int = 250          # caller quiet time that ends a caller turn
    greet_ms: int = 0                  # > 0: agent greets first with a tone this long
    heard: list[str] = field(default_factory=list)       # transcript role=user per turn
    replies: list[str] = field(default_factory=list)     # transcript role=agent per turn
    tool_calls: dict[int, tuple[str, dict]] = field(default_factory=dict)  # turn index -> (tool, args)
    long_reply_turns: set[int] = field(default_factory=set)  # these turns get a 4 s reply (for barge-in)
    stop_delay_ms: int = 200           # barge-in reaction time before `clear`
    send_end_after_turns: int | None = None


class FakeAgent:
    def __init__(self, cfg: FakeAgentConfig | None = None):
        self.cfg = cfg or FakeAgentConfig()
        self.port = 0
        self.starts: list[dict] = []
        self.received_samples = 0
        self.clears = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server = None
        self._ready = threading.Event()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws"

    # ------------------------------------------------------------------ server lifecycle
    def start(self) -> "FakeAgent":
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(10), "fake agent did not start"
        return self

    def stop(self) -> None:
        if self._loop and self._server:
            self._loop.call_soon_threadsafe(self._server.close)
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        from websockets.asyncio.server import serve

        self._loop = asyncio.get_running_loop()
        async with serve(self._handler, "127.0.0.1", 0, max_size=None) as server:
            self._server = server
            self.port = list(server.sockets)[0].getsockname()[1]
            self._ready.set()
            await server.wait_closed()

    # ------------------------------------------------------------------ one call
    async def _handler(self, ws) -> None:
        cfg = self.cfg
        turn = 0
        speaking_until = 0.0      # wall time when the current reply finishes playing at the caller
        caller_loud = False
        last_loud = 0.0
        pending_reply: asyncio.Task | None = None
        session_url = ""

        async def reply(index: int) -> None:
            nonlocal speaking_until
            await asyncio.sleep(cfg.reply_delay_ms / 1000)
            if index < len(cfg.heard):
                await ws.send(json.dumps({"type": "transcript", "role": "user", "text": cfg.heard[index], "final": True}))
            if index in cfg.tool_calls and session_url:
                name, args = cfg.tool_calls[index]
                async with httpx.AsyncClient() as client:
                    r = await client.post(f"{session_url}/tools/{name}", json=args, timeout=5)
                    await ws.send(json.dumps({"type": "tool_call", "name": name, "args": args, "result": r.json()}))
            text = cfg.replies[index] if index < len(cfg.replies) else f"reply {index}"
            await ws.send(json.dumps({"type": "transcript", "role": "agent", "text": text}))
            ms = 4000 if index in cfg.long_reply_turns else cfg.reply_ms
            audio = tone(ms, freq=520)
            speaking_until = time.monotonic() + ms / 1000
            await ws.send(audio.tobytes())
            if cfg.send_end_after_turns is not None and index + 1 >= cfg.send_end_after_turns:
                await asyncio.sleep(ms / 1000 + 0.1)
                await ws.send(json.dumps({"type": "end", "reason": "done"}))

        try:
            async for msg in ws:
                if isinstance(msg, str):
                    data = json.loads(msg)
                    if data.get("type") == "start":
                        self.starts.append(data)
                        session_url = data.get("mock_tools_url", "")
                        await ws.send(json.dumps({"type": "ready"}))
                        if cfg.greet_ms:
                            await ws.send(json.dumps({"type": "transcript", "role": "agent", "text": "Hello from the fake agent."}))
                            await ws.send(tone(cfg.greet_ms, freq=600).tobytes())
                            speaking_until = time.monotonic() + cfg.greet_ms / 1000
                    elif data.get("type") == "hangup":
                        break
                    continue
                samples = from_bytes(msg)
                self.received_samples += len(samples)
                now = time.monotonic()
                loud = rms_dbfs(samples) > -40
                if loud:
                    if now < speaking_until:  # barge-in while our reply plays
                        await asyncio.sleep(cfg.stop_delay_ms / 1000)
                        speaking_until = 0.0
                        self.clears += 1
                        await ws.send(json.dumps({"type": "clear"}))
                    caller_loud = True
                    last_loud = now
                elif caller_loud and now - last_loud >= cfg.end_silence_ms / 1000:
                    caller_loud = False
                    if pending_reply is None or pending_reply.done():
                        pending_reply = asyncio.create_task(reply(turn))
                    turn += 1
        except Exception:
            pass


def two_channel(caller_onsets: list[tuple[int, int]], agent_onsets: list[tuple[int, int]], total_ms: int) -> tuple[np.ndarray, np.ndarray]:
    """Synthetic recording: tones at the given (start_ms, end_ms) spans on each channel."""
    n = total_ms * SAMPLE_RATE // 1000
    c = np.zeros(n, np.int16)
    a = np.zeros(n, np.int16)
    for s, e in caller_onsets:
        c[s * 16: e * 16] = tone(e - s, 300)
    for s, e in agent_onsets:
        a[s * 16: e * 16] = tone(e - s, 500)
    return c, a


def jsonable(x: Any) -> Any:
    return json.loads(json.dumps(x))
