"""ElevenLabs Agents (Conversational AI) client, on the same real time timeline as the pv-ws/1 client.

The call:
1. GET /v1/convai/conversation/get-signed-url with the API key (private agents). Without a key the public
   agent URL is used.
2. Open the WebSocket and send `conversation_initiation_client_data`. The agent is ready when
   `conversation_initiation_metadata` arrives; it names the audio formats of both directions.
3. Every 20 ms tick sends one caller frame as `{"user_audio_chunk": <base64>}`. Agent `audio` events go into
   the play out buffer. `interruption` clears it, `ping` gets a `pong`, and the agent's own transcripts
   (`user_transcript`, `agent_response`, `agent_response_correction`) become transcript messages.
4. Client tools (`client_tool_call`) are answered from the PanchoVoiceAI mock tool server, so tool calls,
   arguments and the final state are checked like for any other agent.
5. After the call, GET /v1/convai/conversations/{id} gives the server side (webhook) tool calls, the cost and
   the termination reason. Webhook tools run on your real backend, so they cannot change the mock state.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from typing import Any

import httpx
import numpy as np

from ..audio.pcm import SAMPLE_RATE, from_bytes, resample
from ..caller import Caller
from ..config import ElevenLabsAgentConfig
from .websocket import AdapterError, AgentMessage, CallRecording, TTSFn, WsCall

SKIP_TOOL_TYPES = {"client", "system"}  # client: already logged by the mock server. system: end_call, ...


# ----------------------------------------------------------------------------- audio formats


def parse_format(fmt: str | None) -> tuple[str, int]:
    """`pcm_16000` -> ("pcm", 16000), `ulaw_8000` -> ("ulaw", 8000). Unknown or missing: pcm 16 kHz."""
    try:
        kind, rate = str(fmt).split("_", 1)
        if kind in ("pcm", "ulaw"):
            return kind, int(rate)
    except (ValueError, TypeError):
        pass
    return "pcm", SAMPLE_RATE


def _to_int16(x: np.ndarray) -> np.ndarray:
    return np.clip(np.round(x), -32768, 32767).astype(np.int16)


def ulaw_decode(data: bytes) -> np.ndarray:
    u = ~np.frombuffer(data, dtype=np.uint8)
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    magnitude = ((mantissa.astype(np.int32) << 3) + 0x84) << exponent
    return np.where(sign != 0, 0x84 - magnitude, magnitude - 0x84).astype(np.int16)


def ulaw_encode(samples: np.ndarray) -> bytes:
    x = np.asarray(samples, dtype=np.int32)
    sign = np.where(x < 0, 0x80, 0)
    mag = np.minimum(np.abs(x), 32635) + 0x84
    exponent = np.floor(np.log2(mag)).astype(np.int32) - 7
    exponent = np.clip(exponent, 0, 7)
    mantissa = (mag >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa) & 0xFF).astype(np.uint8).tobytes()


def decode_agent_audio(raw: bytes, fmt: tuple[str, int]) -> np.ndarray:
    """Agent audio in its declared format -> int16 16 kHz."""
    kind, rate = fmt
    pcm = ulaw_decode(raw) if kind == "ulaw" else from_bytes(raw)
    if rate == SAMPLE_RATE:
        return pcm
    return _to_int16(resample(pcm.astype(np.float32), rate, SAMPLE_RATE))


def encode_caller_audio(frame: np.ndarray, fmt: tuple[str, int]) -> bytes:
    """int16 16 kHz caller frame -> the format the agent asked for."""
    kind, rate = fmt
    x = frame if rate == SAMPLE_RATE else _to_int16(resample(frame.astype(np.float32), SAMPLE_RATE, rate))
    return ulaw_encode(x) if kind == "ulaw" else np.asarray(x, dtype="<i2").tobytes()


# ----------------------------------------------------------------------------- conversation record


def platform_tool_calls(conversation: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Server side tool calls from the conversation record, as harness tool calls, plus notes."""
    calls: list[dict[str, Any]] = []
    notes: list[str] = []
    for item in conversation.get("transcript") or []:
        if not isinstance(item, dict):
            continue
        t_ms = int(round(float(item.get("time_in_call_secs") or 0) * 1000))
        results = {r.get("request_id"): r for r in item.get("tool_results") or [] if isinstance(r, dict)}
        for tc in item.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            name, ttype = str(tc.get("tool_name") or ""), str(tc.get("type") or "")
            if ttype in SKIP_TOOL_TYPES:
                if ttype == "system":
                    notes.append(f"elevenlabs system tool: {name}")
                continue
            try:
                args = json.loads(tc.get("params_as_json") or "{}")
            except (TypeError, ValueError):
                args = {"_raw": tc.get("params_as_json")}
            res = results.get(tc.get("request_id")) or {}
            response: Any = res.get("result_value")
            try:
                response = json.loads(response) if isinstance(response, str) else response
            except ValueError:
                pass
            calls.append({"t_ms": t_ms, "name": name, "args": args if isinstance(args, dict) else {"_value": args},
                          "response": response, "source": f"elevenlabs:{ttype or 'tool'}",
                          "is_error": bool(res.get("is_error"))})
    if calls:
        notes.append(f"{len(calls)} server side tool call(s) ran on your real backend; they are checked by name "
                     "and arguments, but cannot change the mock state")
    return calls, notes


def platform_summary(conversation: dict[str, Any], conversation_id: str) -> dict[str, Any]:
    meta = conversation.get("metadata") or {}
    analysis = conversation.get("analysis") or {}
    return {
        "provider": "elevenlabs",
        "conversation_id": conversation_id,
        "status": conversation.get("status"),
        "call_duration_secs": meta.get("call_duration_secs"),
        "cost_usd": meta.get("cost_fiat"),
        "cost_credits": meta.get("cost"),
        "termination_reason": meta.get("termination_reason"),
        "call_successful": analysis.get("call_successful"),
    }


# ----------------------------------------------------------------------------- the call


class ElevenLabsCall(WsCall):
    def __init__(self, el: ElevenLabsAgentConfig, caller: Caller, tts: TTSFn, *, session_id: str,
                 mock_tools_url: str = "", http_timeout: float = 15.0, **kw: Any):
        super().__init__(el.ws_url, caller, tts, session_id=session_id, mock_tools_url=mock_tools_url, **kw)
        self.el = el
        self.api_key = os.environ.get(el.api_key_env, "")
        self.http_timeout = http_timeout
        self.conversation_id = ""
        self.in_fmt: tuple[str, int] = ("pcm", SAMPLE_RATE)   # what the agent wants from us
        self.out_fmt: tuple[str, int] = ("pcm", SAMPLE_RATE)  # what the agent sends
        self._tool_tasks: set[asyncio.Task] = set()
        self._we_closed = False

    # ------------------------------------------------------------------ http
    def _api(self) -> httpx.AsyncClient:
        headers = {"xi-api-key": self.api_key} if self.api_key else {}
        return httpx.AsyncClient(base_url=self.el.api_url.rstrip("/"), headers=headers, timeout=self.http_timeout)

    async def _signed_url(self) -> str:
        if not self.api_key:
            self.notes.append(f"{self.el.api_key_env} is not set: connecting as a public agent")
            return self.el.ws_url
        params = {"agent_id": self.el.agent_id}
        if self.el.environment:
            params["environment"] = self.el.environment
        if self.el.branch_id:
            params["branch_id"] = self.el.branch_id
        try:
            async with self._api() as api:
                r = await api.get("/v1/convai/conversation/get-signed-url", params=params)
        except httpx.HTTPError as exc:
            raise AdapterError(f"ElevenLabs not reachable at {self.el.api_url}: {exc}") from exc
        if r.status_code in (401, 403):
            raise AdapterError(f"ElevenLabs rejected {self.el.api_key_env} ({r.status_code}): {r.text[:200]}")
        if r.status_code != 200:
            raise AdapterError(f"ElevenLabs signed url error {r.status_code}: {r.text[:200]}")
        url = (r.json() or {}).get("signed_url")
        if not url:
            raise AdapterError("ElevenLabs gave no signed_url")
        return str(url)

    # ------------------------------------------------------------------ transport hooks
    async def _connect(self):
        from websockets.asyncio.client import connect

        url = await self._signed_url()
        try:
            return await connect(url, max_size=None, open_timeout=10, ping_interval=None)
        except Exception as exc:
            raise AdapterError(f"cannot connect to the ElevenLabs agent {self.el.agent_id}: {exc}") from exc

    async def _send_start(self) -> None:
        msg: dict[str, Any] = {"type": "conversation_initiation_client_data"}
        if self.el.dynamic_variables:
            msg["dynamic_variables"] = self.el.dynamic_variables
        if self.el.environment:
            msg["environment"] = self.el.environment
        if self.el.branch_id:
            msg["branch_id"] = self.el.branch_id
        await self._ws.send(json.dumps(msg))

    async def _send_frame(self, frame: np.ndarray) -> None:
        chunk = base64.b64encode(encode_caller_audio(frame, self.in_fmt)).decode("ascii")
        await self._ws.send(json.dumps({"user_audio_chunk": chunk}))

    async def _send_hangup(self) -> None:
        self._we_closed = True
        await self._ws.close()

    # ------------------------------------------------------------------ receive
    def _transcript(self, role: str, text: str, raw_type: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self.messages.append(AgentMessage(self.now_ms, "transcript",
                                          {"role": role, "text": text, "final": True, "source": raw_type}))
        self.on_event({"type": "transcript", "role": role, "text": text, "t_ms": self.now_ms})

    def _correct_last_agent_text(self, corrected: str) -> None:
        for m in reversed(self.messages):
            if m.type == "transcript" and m.data.get("role") == "agent":
                m.data["text"] = corrected.strip()
                m.data["corrected"] = True
                return

    async def _client_tool(self, data: dict[str, Any]) -> None:
        name = str(data.get("tool_name") or "")
        params = data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
        try:
            async with httpx.AsyncClient(timeout=self.http_timeout) as client:
                r = await client.post(f"{self.mock_tools_url}/tools/{name}", json=params)
            result = r.json()
            is_error = r.status_code != 200 or (isinstance(result, dict) and result.get("ok") is False)
        except Exception as exc:  # the agent must still get an answer
            result, is_error = {"ok": False, "error": f"mock tool server: {exc}"}, True
        self.messages.append(AgentMessage(self.now_ms, "tool_call", {"name": name, "args": params, "result": result}))
        if data.get("expects_response", True) and not self.closed:
            reply = {"type": "client_tool_result", "tool_call_id": data.get("tool_call_id"),
                     "result": json.dumps(result), "is_error": bool(is_error)}
            try:
                await self._ws.send(json.dumps(reply))
            except Exception:
                pass

    async def _handle(self, data: dict[str, Any]) -> None:
        mtype = str(data.get("type", ""))
        if mtype == "audio":
            ev = data.get("audio_event") or {}
            b64 = ev.get("audio_base_64")
            if b64:
                self._agent_in.extend(decode_agent_audio(base64.b64decode(b64), self.out_fmt).astype("<i2").tobytes())
            return  # audio is not logged as a message
        if mtype == "ping":
            ev = data.get("ping_event") or {}
            try:
                await self._ws.send(json.dumps({"type": "pong", "event_id": ev.get("event_id")}))
            except Exception:
                pass
            return
        if mtype == "vad_score":
            return
        self.messages.append(AgentMessage(self.now_ms, mtype, data))
        if mtype == "conversation_initiation_metadata":
            ev = data.get("conversation_initiation_metadata_event") or {}
            self.conversation_id = str(ev.get("conversation_id") or "")
            self.in_fmt = parse_format(ev.get("user_input_audio_format"))
            self.out_fmt = parse_format(ev.get("agent_output_audio_format"))
            self.ready = True
        elif mtype == "interruption":
            self._agent_in.clear()
        elif mtype == "user_transcript":
            self._transcript("user", (data.get("user_transcription_event") or {}).get("user_transcript", ""), mtype)
        elif mtype == "agent_response":
            self._transcript("agent", (data.get("agent_response_event") or {}).get("agent_response", ""), mtype)
        elif mtype == "agent_response_correction":
            ev = data.get("agent_response_correction_event") or {}
            if ev.get("corrected_agent_response"):
                self._correct_last_agent_text(ev["corrected_agent_response"])
        elif mtype == "client_tool_call":
            task = asyncio.create_task(self._client_tool(data.get("client_tool_call") or {}))
            self._tool_tasks.add(task)
            task.add_done_callback(self._tool_tasks.discard)
        elif mtype == "client_error":
            ev = data.get("error_event") or {}
            self.notes.append(f"elevenlabs error {ev.get('code')}: {ev.get('message') or ev.get('error_name')}")

    async def _receiver(self) -> None:
        from websockets.exceptions import ConnectionClosed

        try:
            async for msg in self._ws:
                if isinstance(msg, (bytes, bytearray)):
                    continue
                try:
                    data = json.loads(msg)
                except json.JSONDecodeError:
                    self.notes.append(f"elevenlabs sent invalid JSON: {msg[:80]!r}")
                    continue
                if isinstance(data, dict):
                    await self._handle(data)
                await self._notify()
        except ConnectionClosed:
            pass
        finally:
            code = getattr(self._ws, "close_code", None)
            if not self._we_closed and not self._stop:
                if code in (1000, None):
                    self.agent_ended = True  # the agent ended the call (end_call)
                else:
                    reason = getattr(self._ws, "close_reason", "") or ""
                    self.notes.append(f"elevenlabs closed the call with code {code} {reason}".strip())
            self.closed = True
            await self._notify()

    # ------------------------------------------------------------------ conduct and finish
    async def _conduct(self) -> None:
        await super()._conduct()
        # let the last agent audio play out into the recording (the agent may have ended the call)
        deadline = time.monotonic() + 5
        while self._agent_in and time.monotonic() < deadline:
            await asyncio.sleep(0.02)

    async def _fetch_conversation(self) -> dict[str, Any] | None:
        if not (self.el.fetch_conversation and self.api_key and self.conversation_id):
            if self.el.fetch_conversation and not self.api_key:
                self.notes.append(f"{self.el.api_key_env} is not set: server side tool calls and cost are not read")
            return None
        deadline = time.monotonic() + self.el.conversation_wait_s
        last: dict[str, Any] | None = None
        async with self._api() as api:
            while True:
                try:
                    r = await api.get(f"/v1/convai/conversations/{self.conversation_id}")
                    if r.status_code == 200:
                        last = r.json()
                        if str(last.get("status")) in ("done", "failed"):
                            return last
                    elif r.status_code != 404:
                        self.notes.append(f"elevenlabs conversation record error {r.status_code}: {r.text[:200]}")
                        return last
                except httpx.HTTPError as exc:
                    self.notes.append(f"elevenlabs conversation record not reachable: {exc}")
                    return last
                if time.monotonic() >= deadline:
                    self.notes.append("elevenlabs conversation record was not final in time; tool calls may be missing")
                    return last
                await asyncio.sleep(1.0)

    async def run(self) -> CallRecording:
        rec = await super().run()
        if self._tool_tasks:
            await asyncio.wait(list(self._tool_tasks), timeout=5)
        conversation = await self._fetch_conversation()
        rec.platform = {"provider": "elevenlabs", "conversation_id": self.conversation_id}
        if conversation is not None:
            calls, notes = platform_tool_calls(conversation)
            rec.platform_calls = calls
            rec.notes.extend(notes)
            rec.platform = platform_summary(conversation, self.conversation_id)
            rec.platform_record = conversation
        return rec

