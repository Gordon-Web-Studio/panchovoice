"""Live self test: a minimal pv-ws/1 caller, no core package needed.

It starts a mock tool server (contract section 4) on a free port, makes caller lines with
Kokoro, streams them to the agent in real time, records the agent audio, and prints the
transcripts, tool calls, final mock state, and latency per turn.

Run the agent first (`uv run acme-agent`), then:
    uv run python tests/live_call.py                 # booking call + barge-in call
    uv run python tests/live_call.py --call booking
    uv run python tests/live_call.py --call barge
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import wave
from pathlib import Path

import httpx
import numpy as np
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mock_tools import MockBackend, load_fixture, start_server  # noqa: E402

from acme_agent.mouth import resample_24k_to_16k  # noqa: E402

RATE = 16000
FRAME = 640  # 20 ms
OUT_DIR = Path(__file__).resolve().parent / "out"


def tts(text: str, kokoro: str, voice: str) -> bytes:
    r = httpx.post(
        f"{kokoro}/v1/audio/speech",
        json={"model": "kokoro", "input": text, "voice": voice, "response_format": "pcm", "stream": False},
        timeout=60,
    )
    r.raise_for_status()
    return resample_24k_to_16k(r.content)


class Caller:
    def __init__(self, ws) -> None:
        self.ws = ws
        self.t0 = time.monotonic()
        self.pending = bytearray()  # caller audio not yet sent
        self.caller_track = bytearray()
        self.agent_chunks: list[tuple[int, bytes]] = []  # (sample position, pcm)
        self.agent_cursor = 0  # sample position where the next agent audio plays
        self.last_agent_audio = 0.0  # monotonic time of last agent frame
        self.events: list[tuple[int, dict]] = []
        self.clear_times: list[int] = []
        self.closed = False

    def now_ms(self) -> int:
        return int((time.monotonic() - self.t0) * 1000)

    def now_pos(self) -> int:
        return int((time.monotonic() - self.t0) * RATE)

    async def sender(self) -> None:
        n = 0
        silence = b"\x00" * FRAME
        while not self.closed:
            frame = bytes(self.pending[:FRAME]).ljust(FRAME, b"\x00") if self.pending else silence
            del self.pending[:FRAME]
            self.caller_track += frame
            try:
                await self.ws.send(frame)
            except websockets.ConnectionClosed:
                return
            n += 1
            delay = self.t0 + n * 0.020 - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)

    async def receiver(self) -> None:
        try:
            async for msg in self.ws:
                if isinstance(msg, bytes):
                    pos = max(self.agent_cursor, self.now_pos())
                    self.agent_chunks.append((pos, msg))
                    self.agent_cursor = pos + len(msg) // 2
                    self.last_agent_audio = time.monotonic()
                    continue
                data = json.loads(msg)
                t = self.now_ms()
                self.events.append((t, data))
                kind = data.get("type")
                if kind == "transcript":
                    print(f"  [{t:6d} ms] {data['role'].upper():5s}: {data['text']}")
                elif kind == "tool_call":
                    print(f"  [{t:6d} ms] TOOL : {data['name']} {json.dumps(data.get('args'))}")
                elif kind == "clear":
                    self.clear_times.append(t)
                    self.agent_cursor = self.now_pos()  # drop buffered agent audio
                    print(f"  [{t:6d} ms] CLEAR (agent stopped talking)")
                elif kind == "end":
                    print(f"  [{t:6d} ms] END {data.get('reason')}")
        except websockets.ConnectionClosed:
            pass
        finally:
            self.closed = True

    def say(self, pcm: bytes) -> tuple[int, int]:
        """Queue caller audio. Returns (start_ms, end_ms) on the call timeline."""
        start = self.now_ms() + len(self.pending) // 32
        self.pending += pcm
        return start, start + len(pcm) // 32

    def agent_audio_after(self, pos: int) -> int | None:
        """First agent speech onset (ms) at or after sample position pos, by frame energy."""
        for p, chunk in self.agent_chunks:
            if p + len(chunk) // 2 < pos:
                continue
            x = np.frombuffer(chunk, dtype="<i2").astype(np.float32)
            for i in range(0, len(x), 320):
                if p + i >= pos and np.sqrt(np.mean(x[i:i + 320] ** 2) + 1e-9) > 300:
                    return (p + i) * 1000 // RATE
        return None

    async def wait_agent_done(self, since_ms: int, quiet_s: float = 2.0, timeout_s: float = 40.0) -> bool:
        """Wait until the agent spoke after since_ms and then stayed quiet for quiet_s."""
        start = time.monotonic()
        while time.monotonic() - start < timeout_s and not self.closed:
            await asyncio.sleep(0.05)
            spoke = self.last_agent_audio and (self.last_agent_audio - self.t0) * 1000 > since_ms
            playing = self.agent_cursor > self.now_pos()
            if spoke and not playing and time.monotonic() - self.last_agent_audio > quiet_s:
                return True
        return False

    async def wait_agent_starts(self, since_ms: int, timeout_s: float = 30.0) -> int | None:
        start = time.monotonic()
        while time.monotonic() - start < timeout_s:
            await asyncio.sleep(0.01)
            onset = self.agent_audio_after(since_ms * RATE // 1000)
            if onset is not None and onset <= self.now_ms():
                return onset
        return None

    def save(self, name: str) -> Path:
        OUT_DIR.mkdir(exist_ok=True)
        total = max(len(self.caller_track) // 2, self.agent_cursor)
        caller = np.zeros(total, dtype=np.int16)
        c = np.frombuffer(bytes(self.caller_track), dtype="<i2")
        caller[: len(c)] = c
        agent = np.zeros(total, dtype=np.int16)
        clears = [t * RATE // 1000 for t in self.clear_times]
        for p, chunk in self.agent_chunks:
            x = np.frombuffer(chunk, dtype="<i2")
            # Audio scheduled after a clear was dropped by the caller.
            cut = next((cp for cp in clears if p < cp < p + len(x)), None)
            if cut is not None:
                x = x[: cut - p]
            agent[p : p + len(x)] = x[: total - p]
        path = OUT_DIR / f"{name}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(np.stack([caller, agent], axis=1).astype("<i2").tobytes())
        return path


async def run_call(name: str, url: str, kokoro: str, voice: str, lines: list[str], barge: bool) -> dict:
    backend = MockBackend(load_fixture())
    server, mock_url = start_server(backend)
    audio = {line: tts(line, kokoro, voice) for line in lines}
    print(f"\n=== call '{name}' ({'barge-in during greeting' if barge else 'turn by turn'}) ===")
    report = {"name": name, "turns": [], "barge_in": None}
    async with websockets.connect(url, max_size=2**22) as ws:
        caller = Caller(ws)
        await ws.send(json.dumps({"type": "start", "protocol": "pv-ws/1", "session": "selftest", "language": "en",
                                  "mock_tools_url": mock_url, "sample_rate": RATE}))
        tasks = [asyncio.create_task(caller.receiver()), asyncio.create_task(caller.sender())]
        idx = 0
        if barge:
            onset = await caller.wait_agent_starts(0)
            await asyncio.sleep(max(0, (onset + 1000 - caller.now_ms()) / 1000))
            s, e = caller.say(audio[lines[0]])
            print(f"  [{s:6d} ms] (caller starts talking over the greeting: {lines[0]!r})")
            await asyncio.sleep((e - caller.now_ms()) / 1000 + 0.1)
            clear = next((t for t in caller.clear_times if t >= s), None)
            # Last agent audio that the caller actually played before the clear.
            report["barge_in"] = {"caller_start_ms": s, "clear_ms": clear, "stop_ms": None if clear is None else clear - s}
            idx = 1
            latency = await caller.wait_agent_starts(e)
            report["turns"].append({"text": lines[0], "caller_end_ms": e, "agent_start_ms": latency,
                                    "latency_ms": None if latency is None else latency - e})
            await caller.wait_agent_done(e)
        else:
            await caller.wait_agent_done(0)  # greeting
        for line in lines[idx:]:
            s, e = caller.say(audio[line])
            await asyncio.sleep((e - caller.now_ms()) / 1000)
            onset = await caller.wait_agent_starts(e)
            report["turns"].append({"text": line, "caller_end_ms": e, "agent_start_ms": onset,
                                    "latency_ms": None if onset is None else onset - e})
            await caller.wait_agent_done(e)
        await ws.send(json.dumps({"type": "hangup"}))
        await asyncio.sleep(0.3)
        caller.closed = True
        for t in tasks:
            t.cancel()
        path = caller.save(name)
    server.shutdown()
    report["tool_calls"] = backend.calls
    report["final_state"] = {"appointments": backend.state.get("appointments")}
    print("  tool calls (mock API record):")
    for c in backend.calls:
        print(f"    {c['t_ms']:6d} ms {c['name']} {json.dumps(c['args'])} -> {json.dumps(c['response'])}")
    print(f"  final appointments: {json.dumps(report['final_state']['appointments'])}")
    for i, t in enumerate(report["turns"]):
        print(f"  turn {i + 1}: latency end-of-caller-speech to agent speech = {t['latency_ms']} ms  ({t['text']!r})")
    if report["barge_in"]:
        b = report["barge_in"]
        print(f"  barge-in: caller started {b['caller_start_ms']} ms, clear at {b['clear_ms']} ms, stop time {b['stop_ms']} ms")
    print(f"  recording: {path}")
    return report


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="ws://127.0.0.1:8710/ws")
    p.add_argument("--kokoro", default="http://localhost:8880")
    p.add_argument("--voice", default="am_michael", help="caller voice")
    p.add_argument("--call", choices=["booking", "barge", "all"], default="all")
    args = p.parse_args()
    reports = []
    if args.call in ("booking", "all"):
        reports.append(asyncio.run(run_call("booking", args.url, args.kokoro, args.voice, [
            "Hi, I need an appointment on Tuesday at 10 AM.",
            "My name is Sam Rivera.",
            "Yes, that is correct.",
            "Thank you, goodbye.",
        ], barge=False)))
    if args.call in ("barge", "all"):
        reports.append(asyncio.run(run_call("barge", args.url, args.kokoro, args.voice, [
            "Hi, what is my balance? My name is Sam Rivera.",
            "Thank you, goodbye.",
        ], barge=True)))
    lat = [t["latency_ms"] for r in reports for t in r["turns"] if t["latency_ms"] is not None]
    if lat:
        lat.sort()
        print(f"\nlatency over {len(lat)} turns: min {lat[0]} ms, p50 {lat[(len(lat) - 1) // 2]} ms, max {lat[-1]} ms")
    missing = [t for r in reports for t in r["turns"] if t["latency_ms"] is None]
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
