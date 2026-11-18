"""http-turn adapter: test an agent that works one turn at a time over HTTP.

Per caller turn: caller audio (TTS + noise) -> POST transcribe (optional) -> POST chat with the
history -> POST speak (optional) -> decode the reply audio to 16 kHz mono.

Timeline model: there is no live audio stream, so the timeline is built, not paced. The caller
audio of a turn is laid on the caller track; the agent audio starts at caller_end + the wall clock
time the agent needed (transcribe + chat + speak first byte). Both tracks share one timeline, so
the stereo recording, latency, WER and the transcript work as with the WebSocket adapter.
Time spent waiting for the rate limit is not on the timeline.

Every request carries `X-PV-Session` and `X-PV-Mock-Tools-Url`, so an agent can call the mock
tool server like a pv-ws/1 agent reads `mock_tools_url` from `start`.
"""

from __future__ import annotations

import base64
import io
import json
import os
import time
import wave
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx
import numpy as np

from ..audio.pcm import SAMPLE_RATE, ms_to_samples, samples_to_ms, wav_to_pcm16
from ..caller import Caller, HistoryItem
from ..config import HttpConfig
from .websocket import AdapterError, AgentMessage, CallRecording, CallTurn

TTSFn = Callable[[str], np.ndarray]

LEAD_MS = 500  # silence before the first caller turn
GAP_MS = 400   # silence between the end of an agent reply and the next caller turn


# --------------------------------------------------------------------------- helpers


def get_path(data: Any, path: str | None) -> Any:
    """Read a dotted path ("data.reply", "choices.0.text") from parsed JSON. None when missing."""
    if not path:
        return None
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
        if cur is None:
            return None
    return cur


def map_actions(actions: Any) -> list[dict[str, Any]]:
    """Map agent actions [{name|type, args|params|arguments}] to tool calls [{name, args}]."""
    out: list[dict[str, Any]] = []
    if not isinstance(actions, list):
        return out
    for a in actions:
        if not isinstance(a, dict):
            continue
        name = a.get("name") or a.get("type") or a.get("tool")
        if not name:
            continue
        args = a.get("args")
        if args is None:
            args = a.get("params")
        if args is None:
            args = a.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"value": args}
        out.append({"name": str(name), "args": args if isinstance(args, dict) else {}})
    return out


def pcm16_wav_bytes(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(np.asarray(samples, dtype="<i2").tobytes())
    return buf.getvalue()


def decode_audio(data: bytes, content_type: str = "") -> np.ndarray:
    """mp3 or wav bytes -> int16 mono 16 kHz. PCM wav is decoded in numpy, the rest by miniaudio (no ffmpeg)."""
    if not data:
        return np.zeros(0, np.int16)
    if data[:4] == b"RIFF":
        try:
            return wav_to_pcm16(data)
        except Exception:
            pass  # float or other wav: let miniaudio try
    try:
        import miniaudio
    except ImportError as exc:  # pragma: no cover - it is a core dependency
        raise AdapterError("decoding mp3 needs the 'miniaudio' package") from exc
    try:
        dec = miniaudio.decode(data, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1,
                               sample_rate=SAMPLE_RATE)
    except Exception as exc:
        raise AdapterError(f"cannot decode the speak audio ({content_type or 'unknown type'}, {len(data)} bytes): {exc}") from exc
    return np.frombuffer(dec.samples.tobytes(), dtype="<i2").astype(np.int16)


def auth_headers(http: HttpConfig, environ: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ if environ is None else environ
    a = http.auth
    if a.type == "none":
        return {}
    assert a.env
    value = env.get(a.env)
    if not value:
        raise AdapterError(f"auth: env var {a.env} is not set (it must hold the {a.type} secret)")
    if a.type == "bearer":
        return {"Authorization": f"Bearer {value}"}
    if a.type == "cookie":
        return {"Cookie": value}
    assert a.name
    return {a.name: value}


class RateLimiter:
    """At most `per_minute` turns in any 60 s window; `wait()` sleeps when the window is full."""

    def __init__(self, per_minute: int | None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.per_minute = per_minute
        self.clock = clock
        self.sleep = sleep
        self.times: deque[float] = deque()
        self.waited_s = 0.0

    def wait(self) -> float:
        if not self.per_minute:
            return 0.0
        now = self.clock()
        while self.times and now - self.times[0] >= 60:
            self.times.popleft()
        slept = 0.0
        if len(self.times) >= self.per_minute:
            slept = 60 - (now - self.times[0])
            if slept > 0:
                self.sleep(slept)
                self.waited_s += slept
            self.times.popleft()
        self.times.append(self.clock())
        return max(slept, 0.0)


# --------------------------------------------------------------------------- recording


@dataclass
class HttpTurnStats:
    index: int
    stt_ms: int | None
    llm_ms: int | None
    tts_ms: int | None  # speak: time to first audio byte
    pipeline_ms: int | None  # wall clock from the first request to the first agent audio (or the chat reply)
    agent_start_ms: int | None  # timeline ms where the agent reply starts
    agent_end_ms: int | None
    reply_text: str = ""
    heard_text: str = ""
    actions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class HttpCallRecording(CallRecording):
    stats: list[HttpTurnStats] = field(default_factory=list)
    action_calls: list[dict[str, Any]] = field(default_factory=list)  # tool calls mapped from actions
    windows: list[tuple[int, int, int]] = field(default_factory=list)  # (wall_start_ms, wall_end_ms, timeline_ms)
    text_mode: bool = False  # no transcribe step: WER is not measured

    def timeline_ms(self, wall_ms: int) -> int:
        """Map a wall clock ms (since t0, as the mock server logs it) to the timeline."""
        best = 0
        for ws, we, tl in self.windows:
            if wall_ms < ws:
                break
            best = tl + min(wall_ms, we) - ws
        return best


# --------------------------------------------------------------------------- the call


class HttpTurnCall:
    def __init__(
        self,
        http: HttpConfig,
        caller: Caller,
        tts: TTSFn,
        *,
        session_id: str,
        language: str = "en",
        mock_tools_url: str = "",
        max_seconds: float = 180,
        silence_timeout_s: float = 15,
        on_t0: Callable[[float], None] | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        environ: dict[str, str] | None = None,
        clock: Callable[[], float] = time.perf_counter,  # stage times (monotonic ticks 15.6 ms on Windows)
        wall_clock: Callable[[], float] = time.monotonic,  # the mock server clock: t0 and tool call times
        sleep: Callable[[float], None] = time.sleep,
        lead_ms: int = LEAD_MS,
        gap_ms: int = GAP_MS,
        transport: httpx.BaseTransport | None = None,
        **_ignored: Any,  # WebSocket timing keys (end_of_turn_ms, ...) do not apply here
    ):
        self.http = http
        self.caller = caller
        self.tts = tts
        self.session_id = session_id
        self.language = language
        self.mock_tools_url = mock_tools_url
        self.max_samples = int(max_seconds * SAMPLE_RATE)
        self.silence_timeout_ms = int(silence_timeout_s * 1000)
        self.on_t0 = on_t0
        self.on_event = on_event or (lambda e: None)
        self.environ = environ
        self.clock = clock
        self.wall_clock = wall_clock
        self.limiter = RateLimiter(http.max_turns_per_minute, clock, sleep)
        self.lead_ms = lead_ms
        self.gap_ms = gap_ms
        self.transport = transport
        self.t0 = 0.0

    # ------------------------------------------------------------------ http
    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return self.http.base_url.rstrip("/") + "/" + path.lstrip("/")

    def _wall_ms(self) -> int:
        return int(round((self.wall_clock() - self.t0) * 1000))

    def _post(self, client: httpx.Client, stage: str, path: str, **kw: Any) -> httpx.Response:
        url = self._url(path)
        for attempt in range(2):
            try:
                r = client.post(url, **kw)
            except httpx.ConnectError as exc:
                raise AdapterError(f"cannot connect to agent at {url}: {exc}") from exc
            except httpx.TimeoutException as exc:
                raise _TurnFailed(f"{stage}: no answer from {url} within {self.http.timeout_s:g} s") from exc
            if r.status_code == 429 and attempt == 0:
                retry = r.headers.get("Retry-After", "")
                delay = min(float(retry), 60.0) if retry.replace(".", "", 1).isdigit() else 5.0
                self.limiter.sleep(delay)
                continue
            break
        if r.status_code in (401, 403):
            raise AdapterError(f"{stage}: {url} answered {r.status_code}. Check http.auth and its env var")
        if r.status_code >= 400:
            raise _TurnFailed(f"{stage}: {url} answered {r.status_code}: {r.text[:200]}")
        return r

    @staticmethod
    def _json(r: httpx.Response, stage: str) -> Any:
        try:
            return r.json()
        except ValueError as exc:
            raise _TurnFailed(f"{stage}: reply is not JSON: {r.text[:120]!r}") from exc

    def _transcribe(self, client: httpx.Client, audio: np.ndarray) -> str:
        t = self.http.transcribe
        assert t is not None
        wav = pcm16_wav_bytes(audio)
        if t.mode == "multipart":
            r = self._post(client, "transcribe", t.path, files={t.field: ("caller.wav", wav, "audio/wav")},
                           data={k: str(v) for k, v in t.extra_body.items()})
        else:
            body = {t.field: base64.b64encode(wav).decode("ascii"), **t.extra_body}
            r = self._post(client, "transcribe", t.path, json=body)
        return str(get_path(self._json(r, "transcribe"), t.response_text) or "").strip()

    def _chat(self, client: httpx.Client, messages: list[dict[str, str]]) -> tuple[str, Any]:
        c = self.http.chat
        body = {**self.http.extra_body, c.body: messages}
        data = self._json(self._post(client, "chat", c.path, json=body), "chat")
        return str(get_path(data, c.response_text) or "").strip(), get_path(data, c.response_actions)

    def _speak(self, client: httpx.Client, text: str, started: float) -> tuple[np.ndarray, int]:
        """Returns (audio, ms from `started` to the first audio byte)."""
        s = self.http.speak
        assert s is not None
        url = self._url(s.path)
        body = {s.body_text: text, **s.extra_body}
        try:
            with client.stream("POST", url, json=body) as r:
                if r.status_code in (401, 403):
                    raise AdapterError(f"speak: {url} answered {r.status_code}. Check http.auth and its env var")
                if r.status_code >= 400:
                    r.read()
                    raise _TurnFailed(f"speak: {url} answered {r.status_code}: {r.text[:200]}")
                chunks = r.iter_bytes()
                first = next(chunks, b"")
                first_ms = int(round((self.clock() - started) * 1000))
                data = first + b"".join(chunks)
                ctype = r.headers.get("content-type", "")
        except httpx.ConnectError as exc:
            raise AdapterError(f"cannot connect to agent at {url}: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise _TurnFailed(f"speak: no answer from {url} within {self.http.timeout_s:g} s") from exc
        if s.response == "json":
            try:
                payload = json.loads(data)
                data = base64.b64decode(str(get_path(payload, s.audio_field) or ""))
            except (ValueError, TypeError) as exc:
                raise _TurnFailed(f"speak: no base64 audio at '{s.audio_field}'") from exc
            first_ms = int(round((self.clock() - started) * 1000))
            ctype = ""
        return decode_audio(data, ctype), first_ms

    # ------------------------------------------------------------------ run
    def run(self) -> HttpCallRecording:
        headers = {**auth_headers(self.http, self.environ), "X-PV-Session": self.session_id,
                   "X-PV-Mock-Tools-Url": self.mock_tools_url}
        client = httpx.Client(headers=headers, timeout=self.http.timeout_s, transport=self.transport)
        self.t0 = self.wall_clock()
        if self.on_t0:
            self.on_t0(self.t0)
        caller_parts: list[tuple[int, np.ndarray]] = []
        agent_parts: list[tuple[int, np.ndarray]] = []
        turns: list[CallTurn] = []
        messages: list[AgentMessage] = []
        notes: list[str] = []
        stats: list[HttpTurnStats] = []
        action_calls: list[dict[str, Any]] = []
        windows: list[tuple[int, int, int]] = []
        history: list[HistoryItem] = []
        chat_history: list[dict[str, str]] = []
        cur = ms_to_samples(self.lead_ms)
        ended_by = "caller"
        text_mode = self.http.transcribe is None
        try:
            while True:
                utt = self.caller.next_utterance(history)
                if utt is None:
                    break
                if utt.interrupt_after_ms is not None:
                    notes.append(f"turn {len(turns)}: interrupt_after_ms ignored, barge-in is not supported by http-turn")
                if utt.pause_ms:
                    cur += ms_to_samples(utt.pause_ms)
                audio = np.asarray(self.tts(utt.text), dtype=np.int16)
                start, end = cur, cur + len(audio)
                if end > self.max_samples:
                    ended_by = "max_seconds"
                    break
                caller_parts.append((start, audio))
                turn = CallTurn(len(turns), utt.text, start, end, None, utt.pause_ms)
                turns.append(turn)
                history.append(HistoryItem("caller", utt.text))
                self.on_event({"type": "caller_turn", "index": turn.index, "text": utt.text, "t_ms": turn.caller_start_ms})
                end_ms = samples_to_ms(end)

                self.limiter.wait()
                st = HttpTurnStats(turn.index, None, None, None, None, None, None)
                stats.append(st)
                wall_start = self._wall_ms()
                t_req = self.clock()
                try:
                    if not text_mode:
                        heard = self._transcribe(client, audio)
                        st.stt_ms = int(round((self.clock() - t_req) * 1000))
                        st.heard_text = heard
                        messages.append(AgentMessage(end_ms, "transcript",
                                                     {"type": "transcript", "role": "user", "text": heard, "final": True}))
                        self.on_event({"type": "transcript", "role": "user", "text": heard, "t_ms": end_ms})
                    else:
                        heard = utt.text
                    chat_history.append({"role": "user", "content": heard})
                    t_chat = self.clock()
                    reply, actions = self._chat(client, chat_history)
                    st.llm_ms = int(round((self.clock() - t_chat) * 1000))
                    st.reply_text = reply
                    agent_audio = np.zeros(0, np.int16)
                    if reply and self.http.speak is not None:
                        t_speak = self.clock()
                        agent_audio, st.tts_ms = self._speak(client, reply, t_speak)
                        st.pipeline_ms = int(round((t_speak - t_req) * 1000)) + st.tts_ms
                    else:
                        st.pipeline_ms = int(round((self.clock() - t_req) * 1000))
                except _TurnFailed as exc:
                    notes.append(f"turn {turn.index}: {exc}")
                    windows.append((wall_start, self._wall_ms(), end_ms))
                    turn.agent_replied = False
                    cur = end + ms_to_samples(self.gap_ms)
                    if utt.end_call:
                        break
                    continue
                windows.append((wall_start, self._wall_ms(), end_ms))
                mapped = map_actions(actions) if self.http.tools_from == "actions" else []
                st.actions = mapped
                tool_ms = end_ms + (st.stt_ms or 0) + st.llm_ms
                for m in mapped:
                    call = {"t_ms": tool_ms, "name": m["name"], "args": m["args"], "response": None, "source": "actions"}
                    action_calls.append(call)
                    messages.append(AgentMessage(tool_ms, "tool_call", {"type": "tool_call", **m}))
                if not reply:
                    notes.append(f"turn {turn.index}: the chat reply had no text at '{self.http.chat.response_text}'")
                    turn.agent_replied = False
                    cur = end + ms_to_samples(self.gap_ms)
                    if utt.end_call:
                        break
                    continue
                if st.pipeline_ms > self.silence_timeout_ms:
                    notes.append(f"turn {turn.index}: agent needed {st.pipeline_ms} ms, more than the silence timeout "
                                 f"{self.silence_timeout_ms} ms")
                a_start = end + ms_to_samples(st.pipeline_ms)
                st.agent_start_ms = samples_to_ms(a_start)
                st.agent_end_ms = samples_to_ms(a_start + len(agent_audio))
                if len(agent_audio):
                    agent_parts.append((a_start, agent_audio))
                turn.agent_replied = True
                messages.append(AgentMessage(st.agent_start_ms, "transcript",
                                             {"type": "transcript", "role": "agent", "text": reply}))
                history.append(HistoryItem("agent", reply))
                chat_history.append({"role": "assistant", "content": reply})
                self.on_event({"type": "agent_turn", "index": turn.index, "text": reply, "t_ms": st.agent_start_ms})
                cur = a_start + len(agent_audio) + ms_to_samples(self.gap_ms)
                if cur > self.max_samples:
                    ended_by = "max_seconds"
                    break
                if utt.end_call:
                    break
        finally:
            client.close()
        if self.limiter.waited_s:
            notes.append(f"rate limit: waited {self.limiter.waited_s:.1f} s in total (not on the timeline)")
        if not turns:
            ended_by = "no_turns" if ended_by == "caller" else ended_by
        total = max([s + len(a) for s, a in caller_parts + agent_parts], default=0) + ms_to_samples(self.gap_ms)
        caller_track = np.zeros(total, np.int16)
        agent_track = np.zeros(total, np.int16)
        for track, parts in ((caller_track, caller_parts), (agent_track, agent_parts)):
            for s, a in parts:
                n = max(0, min(len(a), total - s))
                track[s:s + n] = a[:n]
        messages.sort(key=lambda m: m.t_ms)
        return HttpCallRecording(caller_track, agent_track, turns, messages, self.t0, ended_by, notes, False,
                                 stats=stats, action_calls=action_calls, windows=windows, text_mode=text_mode)


class _TurnFailed(RuntimeError):
    """One turn failed on the agent side (HTTP error, bad reply). The call goes on."""
