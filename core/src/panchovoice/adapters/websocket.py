"""pv-ws/1 client (contract section 5) with real time pacing.

Timeline model: a 20 ms ticker is the clock. On every tick it sends one caller frame
(speech or zeros) and plays out one agent frame from the receive buffer (or zeros).
Both tracks grow by exactly 320 samples per tick, so they share one timeline from t0
(the moment the WebSocket connects) and every time is an exact sample index.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from ..audio.pcm import FRAME_BYTES, FRAME_SAMPLES, SAMPLE_RATE, from_bytes, frame_is_loud, ms_to_samples, samples_to_ms
from ..caller import Caller, HistoryItem, ScriptedCaller

PROTOCOL = "pv-ws/1"


class AdapterError(RuntimeError):
    """The harness could not run the call (agent down, no ready)."""


@dataclass
class AgentMessage:
    t_ms: int
    type: str
    data: dict[str, Any]


@dataclass
class CallTurn:
    index: int
    caller_text: str
    caller_start_sample: int
    caller_end_sample: int
    interrupt_after_ms: int | None = None
    pause_ms: int | None = None
    agent_replied: bool | None = None  # set after waiting for the agent

    @property
    def caller_start_ms(self) -> int:
        return samples_to_ms(self.caller_start_sample)

    @property
    def caller_end_ms(self) -> int:
        return samples_to_ms(self.caller_end_sample)


@dataclass
class CallRecording:
    caller_track: np.ndarray
    agent_track: np.ndarray
    turns: list[CallTurn]
    messages: list[AgentMessage]
    t0: float  # time.monotonic() at connect
    ended_by: str = "caller"  # caller | agent | max_seconds | agent_closed | no_turns
    notes: list[str] = field(default_factory=list)
    greeting: bool = False  # the agent spoke before the first caller turn

    @property
    def duration_ms(self) -> int:
        return samples_to_ms(len(self.caller_track))


TTSFn = Callable[[str], np.ndarray]


class WsCall:
    def __init__(
        self,
        url: str,
        caller: Caller,
        tts: TTSFn,
        *,
        session_id: str,
        language: str = "en",
        mock_tools_url: str = "",
        max_seconds: float = 180,
        silence_timeout_s: float = 15,
        end_of_turn_ms: int = 1200,
        transcript_end_ms: int = 600,
        first_turn_wait_ms: int = 2500,
        ready_timeout_s: float = 10,
        loud_dbfs: float = -45.0,
        on_t0: Callable[[float], None] | None = None,
        agent_text_fallback: Callable[[np.ndarray], str] | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.url = url
        self.caller = caller
        self.tts = tts
        self.session_id = session_id
        self.language = language
        self.mock_tools_url = mock_tools_url
        self.max_samples = int(max_seconds * SAMPLE_RATE)
        self.silence_timeout_ms = int(silence_timeout_s * 1000)
        self.end_of_turn_samples = ms_to_samples(end_of_turn_ms)
        self.transcript_end_samples = ms_to_samples(transcript_end_ms)
        self.first_turn_wait_ms = first_turn_wait_ms
        self.ready_timeout_s = ready_timeout_s
        self.loud_dbfs = loud_dbfs
        self.on_t0 = on_t0
        self.agent_text_fallback = agent_text_fallback
        self.on_event = on_event or (lambda e: None)

        # timeline state
        self.sample = 0
        self._caller_frames: list[np.ndarray] = []
        self._agent_frames: list[np.ndarray] = []
        self._caller_buf = np.zeros(0, dtype=np.int16)
        self._caller_pos = 0
        self._agent_in = bytearray()
        self.agent_last_loud = -1  # sample index just after the last loud agent frame
        self.agent_first_loud_after: dict[int, int] = {}
        self._loud_onsets: list[int] = []  # start samples of loud agent frames that follow a quiet frame
        self._prev_loud = False

        self.messages: list[AgentMessage] = []
        self.turns: list[CallTurn] = []
        self.notes: list[str] = []
        self.ready = False
        self.agent_ended = False
        self.closed = False
        self.ended_by = "caller"
        self.greeting = False
        self._stop = False
        self._cond: asyncio.Condition | None = None
        self._ws = None
        self.t0 = 0.0

    # ------------------------------------------------------------------ helpers
    @property
    def now_ms(self) -> int:
        return samples_to_ms(self.sample)

    def _time_up(self) -> bool:
        return self.sample >= self.max_samples

    async def _wait(self, pred: Callable[[], bool], timeout_ms: int | None) -> bool:
        """Wait (in timeline samples) until pred() is true. False on timeout, call end or time limit."""
        assert self._cond is not None
        deadline = self.sample + ms_to_samples(timeout_ms) if timeout_ms is not None else None
        async with self._cond:
            while True:
                if pred():
                    return True
                if self._stop or self.closed or self._time_up():
                    return False
                if deadline is not None and self.sample >= deadline:
                    return False
                await self._cond.wait()

    # ------------------------------------------------------------------ tasks
    async def _ticker(self) -> None:
        loop = asyncio.get_running_loop()
        start = loop.time()
        k = 0
        while not self._stop:
            due = int((loop.time() - start) / 0.02) - k
            if due <= 0:
                await asyncio.sleep(start + (k + 1) * 0.02 - loop.time())
                continue
            # after a stall, tick every missed frame so the sample clock stays equal to wall time
            for _ in range(due):
                await self._tick_once()
                k += 1
            assert self._cond is not None
            async with self._cond:
                self._cond.notify_all()

    async def _tick_once(self) -> None:
        # caller channel
        if self._caller_pos < len(self._caller_buf):
            frame = self._caller_buf[self._caller_pos:self._caller_pos + FRAME_SAMPLES]
            self._caller_pos += len(frame)
            if len(frame) < FRAME_SAMPLES:
                frame = np.concatenate([frame, np.zeros(FRAME_SAMPLES - len(frame), dtype=np.int16)])
        else:
            frame = np.zeros(FRAME_SAMPLES, dtype=np.int16)
        self._caller_frames.append(frame)
        if self.ready and not self.closed and self._ws is not None:
            try:
                await self._ws.send(frame.astype("<i2").tobytes())
            except Exception:
                self.closed = True
        # agent channel: play out one frame of the buffer
        if len(self._agent_in) >= FRAME_BYTES:
            chunk = bytes(self._agent_in[:FRAME_BYTES])
            del self._agent_in[:FRAME_BYTES]
            aframe = from_bytes(chunk)
        elif self._agent_in:
            chunk = bytes(self._agent_in)
            self._agent_in.clear()
            aframe = np.concatenate([from_bytes(chunk), np.zeros(FRAME_SAMPLES - len(chunk) // 2, dtype=np.int16)])
        else:
            aframe = np.zeros(FRAME_SAMPLES, dtype=np.int16)
        self._agent_frames.append(aframe)
        loud = frame_is_loud(aframe, self.loud_dbfs)
        if loud:
            if not self._prev_loud:
                self._loud_onsets.append(self.sample)
            self.agent_last_loud = self.sample + FRAME_SAMPLES
        self._prev_loud = loud
        self.sample += FRAME_SAMPLES

    async def _receiver(self) -> None:
        from websockets.exceptions import ConnectionClosed

        try:
            async for msg in self._ws:
                if isinstance(msg, (bytes, bytearray)):
                    self._agent_in.extend(msg)
                    continue
                try:
                    data = json.loads(msg)
                except json.JSONDecodeError:
                    self.notes.append(f"agent sent invalid JSON: {msg[:80]!r}")
                    continue
                if not isinstance(data, dict):
                    continue
                mtype = str(data.get("type", ""))
                self.messages.append(AgentMessage(self.now_ms, mtype, data))
                if mtype == "ready":
                    self.ready = True
                elif mtype == "clear":
                    self._agent_in.clear()
                elif mtype == "end":
                    self.agent_ended = True
                elif mtype == "transcript":
                    self.on_event({"type": "transcript", "role": data.get("role"), "text": data.get("text"), "t_ms": self.now_ms})
                if self._cond is not None:
                    async with self._cond:
                        self._cond.notify_all()
        except ConnectionClosed:
            pass
        finally:
            self.closed = True
            if self._cond is not None:
                async with self._cond:
                    self._cond.notify_all()

    # ------------------------------------------------------------------ turn logic
    def _agent_audio_drained(self) -> bool:
        return len(self._agent_in) == 0

    def _agent_text_since(self, start_ms: int) -> str:
        parts = [m.data.get("text", "") for m in self.messages
                 if m.type == "transcript" and m.data.get("role") == "agent" and m.t_ms >= start_ms]
        return " ".join(p.strip() for p in parts if p).strip()

    async def _wait_agent_onset(self, after_sample: int, timeout_ms: int) -> int | None:
        """Wait for agent audio after `after_sample`. Returns the onset sample (or None)."""
        def has_onset() -> bool:
            return self.agent_last_loud > after_sample or self.agent_ended

        ok = await self._wait(has_onset, timeout_ms)
        if not ok or self.agent_last_loud <= after_sample:
            return None
        onsets = [s for s in self._loud_onsets if s >= after_sample]
        return onsets[0] if onsets else after_sample  # agent was already talking when we started waiting

    async def _wait_agent_turn(self, after_sample: int, timeout_ms: int) -> bool:
        """Wait until the agent replied and finished. False when it never started."""
        onset = await self._wait_agent_onset(after_sample, timeout_ms)
        if onset is None:
            return False
        start_ms = samples_to_ms(after_sample)

        def finished() -> bool:
            if self.agent_ended and self._agent_audio_drained():
                return True
            if not self._agent_audio_drained():
                return False
            quiet = self.sample - self.agent_last_loud
            if quiet >= self.end_of_turn_samples:
                return True
            return quiet >= self.transcript_end_samples and bool(self._agent_text_since(start_ms))

        await self._wait(finished, None)
        return True

    async def _speak(self, audio: np.ndarray) -> tuple[int, int]:
        start = self.sample
        self._caller_buf = np.asarray(audio, dtype=np.int16)
        self._caller_pos = 0
        end = start + len(self._caller_buf)
        await self._wait(lambda: self.sample >= end, None)
        return start, end

    async def _agent_reply_text(self, from_sample: int) -> str:
        text = self._agent_text_since(samples_to_ms(from_sample))
        if text or self.agent_text_fallback is None:
            return text
        seg = np.concatenate(self._agent_frames[from_sample // FRAME_SAMPLES:]) if self._agent_frames else np.zeros(0, np.int16)
        try:
            return await asyncio.to_thread(self.agent_text_fallback, seg)
        except Exception as exc:  # STT is a fallback only
            self.notes.append(f"stt fallback failed: {exc}")
            return ""

    def _peek_interrupt(self) -> int | None:
        if isinstance(self.caller, ScriptedCaller) and self.caller.turns_said < len(self.caller.turns):
            return self.caller.turns[self.caller.turns_said].interrupt_after_ms
        return None

    async def _conduct(self) -> None:
        history: list[HistoryItem] = []
        # the agent may greet first: wait for it, or a short silence
        greeted = await self._wait_agent_turn(0, self.first_turn_wait_ms)
        if greeted:
            text = await self._agent_reply_text(0)
            if text:
                history.append(HistoryItem("agent", text))
        self.greeting = greeted
        last_end = self.sample
        while not (self._time_up() or self.closed):
            if self.agent_ended and self._agent_audio_drained():
                self.ended_by = "agent"
                return
            interrupt_ms = self._peek_interrupt()
            utt = await asyncio.to_thread(self.caller.next_utterance, history)
            if utt is None:
                break
            audio = await asyncio.to_thread(self.tts, utt.text)
            if interrupt_ms is not None and self.turns:
                # barge-in: wait for the agent to start replying, then talk over it
                onset = await self._wait_agent_onset(last_end, self.silence_timeout_ms)
                if onset is None:
                    self.notes.append(f"turn {len(self.turns)}: agent did not reply, interruption not exercised")
                    self.turns[-1].agent_replied = False
                else:
                    self.turns[-1].agent_replied = True
                    target = onset + ms_to_samples(utt.interrupt_after_ms or 0)
                    await self._wait(lambda: self.sample >= target, None)
            if utt.pause_ms:
                target = self.sample + ms_to_samples(utt.pause_ms)
                await self._wait(lambda: self.sample >= target, None)
            if self._time_up() or self.closed:
                break
            start, end = await self._speak(audio)
            turn = CallTurn(len(self.turns), utt.text, start, end, utt.interrupt_after_ms, utt.pause_ms)
            self.turns.append(turn)
            history.append(HistoryItem("caller", utt.text))
            self.on_event({"type": "caller_turn", "index": turn.index, "text": utt.text, "t_ms": turn.caller_start_ms})
            last_end = end
            if self._peek_interrupt() is not None:
                continue  # next turn interrupts the reply: do not wait for the agent to finish
            replied = await self._wait_agent_turn(end, self.silence_timeout_ms)
            turn.agent_replied = replied
            if replied:
                text = await self._agent_reply_text(end)
                if text:
                    history.append(HistoryItem("agent", text))
                    self.on_event({"type": "agent_turn", "index": turn.index, "text": text, "t_ms": self.now_ms})
            else:
                self.notes.append(f"turn {turn.index}: no agent reply within {self.silence_timeout_ms} ms")
            if utt.end_call:
                break
        if self._time_up():
            self.ended_by = "max_seconds"
        elif self.closed and not self.agent_ended:
            self.ended_by = "agent_closed"
        elif self.agent_ended:
            self.ended_by = "agent"

    # ------------------------------------------------------------------ entry
    async def run(self) -> CallRecording:
        from websockets.asyncio.client import connect

        self._cond = asyncio.Condition()
        try:
            ws = await connect(self.url, max_size=None, open_timeout=10, ping_interval=None)
        except Exception as exc:
            raise AdapterError(f"cannot connect to agent at {self.url}: {exc}") from exc
        self._ws = ws
        self.t0 = time.monotonic()
        if self.on_t0:
            self.on_t0(self.t0)
        start_msg = {"type": "start", "protocol": PROTOCOL, "session": self.session_id, "language": self.language,
                     "mock_tools_url": self.mock_tools_url, "sample_rate": SAMPLE_RATE}
        recv_task = asyncio.create_task(self._receiver())
        tick_task = asyncio.create_task(self._ticker())
        try:
            await ws.send(json.dumps(start_msg))
            ok = await self._wait(lambda: self.ready, int(self.ready_timeout_s * 1000))
            if not ok:
                raise AdapterError("agent closed the connection before ready" if self.closed
                                   else f"agent did not send ready within {self.ready_timeout_s:g} s")
            await self._conduct()
        finally:
            try:
                if not self.closed:
                    await ws.send(json.dumps({"type": "hangup"}))
            except Exception:
                pass
            self._stop = True
            if self._cond is not None:
                async with self._cond:
                    self._cond.notify_all()
            try:
                await asyncio.wait_for(tick_task, 2)
            except Exception:
                tick_task.cancel()
            try:
                await ws.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(recv_task, 2)
            except Exception:
                recv_task.cancel()
        if not self.turns:
            self.ended_by = "no_turns" if self.ended_by == "caller" else self.ended_by
        caller = np.concatenate(self._caller_frames) if self._caller_frames else np.zeros(0, np.int16)
        agent = np.concatenate(self._agent_frames) if self._agent_frames else np.zeros(0, np.int16)
        n = min(len(caller), len(agent))
        return CallRecording(caller[:n], agent[:n], self.turns, self.messages, self.t0, self.ended_by, self.notes,
                             self.greeting)
