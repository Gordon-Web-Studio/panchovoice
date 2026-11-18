"""One phone call over pv-ws/1: wires ears, brain, mouth and tools together, plus barge-in."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass

import httpx
import numpy as np
from websockets.exceptions import ConnectionClosed

from .brain import Brain, Timing
from .ears import Ears, Endpointer, Transcriber
from .mouth import Mouth
from .prompts import greeting, system_prompt
from .tools import MockTools

log = logging.getLogger("acme_agent")

FRAME_BYTES = 640  # 20 ms of PCM16 at 16 kHz
FRAME_S = 0.020
SEND_LEAD_S = 0.12  # how far ahead of real time the agent may send audio
BARGE_IN_MS = 300  # caller speech while the agent talks that counts as an interruption
SPECULATE_MS = 320  # silence after which STT starts early (the turn still ends at end_silence_ms)


@dataclass
class Settings:
    ollama_url: str = "http://localhost:11434"
    kokoro_url: str = "http://localhost:8880"
    model: str = "qwen2.5:7b"
    voice: str = "af_heart"
    language: str = "en"
    bug: bool = True
    end_silence_ms: int = 700


class Call:
    def __init__(self, ws, settings: Settings, transcriber: Transcriber, http: httpx.AsyncClient, call_id: int) -> None:
        self.ws = ws
        self.s = settings
        self.transcriber = transcriber
        self.http = http
        self.id = call_id
        self.language = settings.language
        self.ears = Ears(Endpointer(end_silence_ms=settings.end_silence_ms))
        self.brain = Brain(http, settings.ollama_url, settings.model, confirm_gate=not settings.bug)
        self.mouth = Mouth(http, settings.kokoro_url, settings.voice)
        self.tools = MockTools(http, None)
        self.messages: list[dict] = []
        self.turn: asyncio.Task | None = None
        self.playout_end = 0.0  # monotonic time when the caller finishes playing our audio
        self.started = False
        self.turn_no = 0
        self._spec: tuple[asyncio.Future, int] | None = None  # (speculative STT, speech epoch)

    # ---- helpers -------------------------------------------------------------------------

    @property
    def speaking(self) -> bool:
        return time.monotonic() < self.playout_end

    async def send_json(self, obj: dict) -> None:
        with contextlib.suppress(ConnectionClosed):
            await self.ws.send(json.dumps(obj))

    def _log(self, msg: str, *args) -> None:
        log.info("[call %d] " + msg, self.id, *args)

    # ---- main loop -----------------------------------------------------------------------

    async def run(self) -> None:
        self._log("connected")
        try:
            async for msg in self.ws:
                if isinstance(msg, bytes):
                    if self.started:
                        self.on_audio(msg)
                    continue
                try:
                    data = json.loads(msg)
                except ValueError:
                    continue
                kind = data.get("type")
                if kind == "start":
                    await self.on_start(data)
                elif kind == "hangup":
                    self._log("caller hung up")
                    break
        except ConnectionClosed:
            self._log("connection closed")
        finally:
            await self.cancel_turn()
            with contextlib.suppress(Exception):
                await self.ws.close()
            self._log("ended")

    async def on_start(self, data: dict) -> None:
        self.language = data.get("language") or self.s.language
        self.tools = MockTools(self.http, data.get("mock_tools_url"))
        await self.send_json({"type": "ready"})
        try:
            specs = await self.tools.load()
            self._log("tools: %s", ", ".join(s["name"] for s in specs) or "none")
        except (httpx.HTTPError, ValueError) as e:
            self._log("could not load tools from %s: %s", self.tools.base, e)
        self.messages = [{"role": "system", "content": system_prompt(self.s.bug, self.language)}]
        self.started = True
        text = greeting(self.language)
        self.messages.append({"role": "assistant", "content": text})
        self.turn = asyncio.create_task(self.speak_only(text))

    def on_audio(self, pcm: bytes) -> None:
        ep = self.ears.endpointer
        for ev in self.ears.feed(pcm):
            if ev.kind == "start":
                self._log("caller speech start (agent speaking: %s)", self.speaking)
            elif ev.kind == "end":
                spec, self._spec = self._spec, None
                if self.speaking:
                    # Short speech under the agent that did not trigger barge-in (a cough, "mm-hm").
                    self._log("ignored %d ms of speech while agent talks", ev.voiced_ms)
                    continue
                # Reuse the early transcript only if no speech came back after it started.
                reuse = spec[0] if spec and spec[1] == ep.epoch else None
                self.start_turn(ev.audio, reuse)
            elif ev.kind == "blip":
                self._spec = None
                self._log("ignored blip (%d ms)", ev.voiced_ms)
        if ep.in_speech:
            # Speculative STT: start transcribing during the end of turn silence. If the caller
            # stays quiet until the turn ends, the result is ready sooner.
            if (self._spec is None or self._spec[1] != ep.epoch) and ep.silence_ms >= SPECULATE_MS \
                    and ep.voiced_ms >= ep.min_voiced_ms and not self.speaking:
                audio = ep.snapshot()
                fut = asyncio.ensure_future(asyncio.to_thread(self.transcriber.transcribe, audio, self.language))
                self._spec = (fut, ep.epoch)
        if self.speaking and ep.in_speech and ep.voiced_ms >= BARGE_IN_MS:
            self.barge_in()

    def barge_in(self) -> None:
        """Stop talking now. Runs inside the audio handler, so the turn task is paused at an
        await and cancel() takes effect before it can send another frame."""
        self._log("BARGE-IN: caller talked over the agent for %d ms, stopping audio", self.ears.endpointer.voiced_ms)
        if self.turn and not self.turn.done():
            self.turn.cancel()
        self.playout_end = 0.0
        asyncio.get_running_loop().create_task(self.send_json({"type": "clear"}))

    async def cancel_turn(self) -> None:
        if self.turn and not self.turn.done():
            self.turn.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.turn
        self.turn = None

    def start_turn(self, audio: np.ndarray, speculative: asyncio.Future | None = None) -> None:
        if self.turn and not self.turn.done():
            self._log("new caller speech before the reply: restarting the turn")
            self.turn.cancel()
        self.turn_no += 1
        self.turn = asyncio.create_task(self.run_turn(audio, self.turn_no, speculative))

    # ---- one turn: STT -> LLM (+tools) -> TTS -> audio -----------------------------------

    async def run_turn(self, audio: np.ndarray, n: int, speculative: asyncio.Future | None = None) -> None:
        t_end = time.perf_counter()  # end of turn detected (end_silence_ms after last speech)
        if speculative is not None:
            text = await speculative
        else:
            text = await asyncio.to_thread(self.transcriber.transcribe, audio, self.language)
        stt_ms = int((time.perf_counter() - t_end) * 1000)
        if speculative is not None:
            self._log("turn %d: used speculative transcript (waited %d ms after end of turn)", n, stt_ms)
        if not text:
            self._log("turn %d: empty transcript, ignored", n)
            return
        await self.send_json({"type": "transcript", "role": "user", "text": text, "final": True})
        self._log("turn %d user: %s", n, text)
        self.messages.append({"role": "user", "content": text})

        timing = Timing()
        t_llm = time.perf_counter()
        sentences: asyncio.Queue = asyncio.Queue()
        first_audio: dict = {}

        async def think() -> None:
            try:
                async for ev in self.brain.respond(self.messages, self.tools, timing, self.language):
                    if ev.kind == "say":
                        await sentences.put(ev.text)
                    elif ev.kind == "guard":
                        self._log("turn %d GUARD: blocked a '%s' claim with no successful tool call", n, ev.text)
                    elif ev.kind == "nudge":
                        self._log("turn %d NUDGE: caller confirmed %s, told the model to book now", n, ev.text)
                    elif ev.kind == "gate":
                        self._log("turn %d GATE: held %s %s (no read back + yes yet)", n, ev.name, json.dumps(ev.args))
                    else:
                        self._log("turn %d tool %s %s -> %s", n, ev.name, json.dumps(ev.args), json.dumps(ev.result))
                        await self.send_json({"type": "tool_call", "name": ev.name, "args": ev.args})
            except httpx.HTTPError as e:
                self._log("turn %d LLM error: %s", n, e)
                await sentences.put("Sorry, I am having trouble right now. Please try again in a moment.")
            finally:
                await sentences.put(None)

        speak_task = asyncio.create_task(self.speak_queue(sentences, first_audio))
        try:
            await think()
            await speak_task
        finally:
            speak_task.cancel()

        tts_ms = first_audio.get("tts_ms")
        first_ms = int((first_audio["t"] - t_end) * 1000) if "t" in first_audio else None
        v2v = first_ms + self.s.end_silence_ms if first_ms is not None else None
        self._log(
            "TIMING turn %d: stt %d ms | llm first sentence %s ms, total %d ms, rounds %d | "
            "tts first byte %s ms | first audio %s ms after end of turn | est. voice to voice %s ms",
            n, stt_ms, timing.first_sentence_ms, timing.total_ms or int((time.perf_counter() - t_llm) * 1000),
            timing.rounds, tts_ms, first_ms, v2v,
        )

    async def speak_only(self, text: str) -> None:
        q: asyncio.Queue = asyncio.Queue()
        await q.put(text)
        await q.put(None)
        await self.speak_queue(q, {})

    async def speak_queue(self, sentences: asyncio.Queue, first_audio: dict) -> None:
        """Synthesize sentences one after the other; synthesis of the next overlaps playback."""
        audio_q: asyncio.Queue = asyncio.Queue(maxsize=4)

        async def synth() -> None:
            try:
                while (text := await sentences.get()) is not None:
                    t = time.perf_counter()
                    chunks = []
                    async for chunk in self.mouth.stream(text):
                        if "tts_ms" not in first_audio:
                            first_audio["tts_ms"] = int((time.perf_counter() - t) * 1000)
                        chunks.append(chunk)
                    await audio_q.put((text, b"".join(chunks)))
            except httpx.HTTPError as e:
                self._log("TTS error: %s", e)
            finally:
                await audio_q.put(None)

        synth_task = asyncio.create_task(synth())
        try:
            while (item := await audio_q.get()) is not None:
                text, pcm = item
                await self.send_json({"type": "transcript", "role": "agent", "text": text})
                self._log("agent: %s", text)
                await self.send_audio(pcm, first_audio)
        finally:
            synth_task.cancel()

    async def send_audio(self, pcm: bytes, first_audio: dict) -> None:
        for i in range(0, len(pcm), FRAME_BYTES):
            now = time.monotonic()
            if self.playout_end < now:
                self.playout_end = now
            ahead = self.playout_end - now
            if ahead > SEND_LEAD_S:
                await asyncio.sleep(ahead - SEND_LEAD_S)
            frame = pcm[i : i + FRAME_BYTES]
            await self.ws.send(frame)
            if "t" not in first_audio:
                first_audio["t"] = time.perf_counter()
            self.playout_end += len(frame) / 32000
