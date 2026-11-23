"""A tiny fake of the ElevenLabs Agents API for tests: signed URL, the conversation WebSocket, and the
conversation record. One websockets server answers both the HTTP endpoints and the WebSocket.

Per caller turn (caller audio loud, then quiet for end_silence_ms) it sends `user_transcript`, optionally a
`client_tool_call` (and waits for `client_tool_result`), then `agent_response` and the reply audio.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs, urlparse

import numpy as np

from panchovoice.adapters.elevenlabs import decode_agent_audio, encode_caller_audio, parse_format
from panchovoice.audio.pcm import rms_dbfs, tone


@dataclass
class FakeElevenLabsConfig:
    api_key: str = "test-key"
    agent_id: str = "agent_test"
    in_format: str = "pcm_16000"    # user_input_audio_format
    out_format: str = "pcm_16000"   # agent_output_audio_format
    reply_delay_ms: int = 300
    reply_ms: int = 700
    end_silence_ms: int = 250
    greet_ms: int = 0
    heard: list[str] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)
    client_tools: dict[int, tuple[str, dict]] = field(default_factory=dict)  # turn -> (tool, params)
    webhook_tools: list[dict[str, Any]] = field(default_factory=list)  # tool_calls put in the record
    end_after_turns: int | None = None  # close with 1000 after this many replies (the agent ends the call)
    record_status: str = "done"


class FakeElevenLabs:
    def __init__(self, cfg: FakeElevenLabsConfig | None = None):
        self.cfg = cfg or FakeElevenLabsConfig()
        self.port = 0
        self.client_data: list[dict] = []
        self.pongs: list[dict] = []
        self.tool_results: list[dict] = []
        self.signed_url_keys: list[str | None] = []
        self.record_gets = 0
        self.received_samples = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server = None
        self._ready = threading.Event()

    @property
    def api_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "FakeElevenLabs":
        self._thread = threading.Thread(target=lambda: asyncio.run(self._main()), daemon=True)
        self._thread.start()
        assert self._ready.wait(10), "fake ElevenLabs did not start"
        return self

    def stop(self) -> None:
        if self._loop and self._server:
            self._loop.call_soon_threadsafe(self._server.close)
        if self._thread:
            self._thread.join(timeout=5)

    async def _main(self) -> None:
        from websockets.asyncio.server import serve

        self._loop = asyncio.get_running_loop()
        async with serve(self._handler, "127.0.0.1", 0, max_size=None, process_request=self._http) as server:
            self._server = server
            self.port = list(server.sockets)[0].getsockname()[1]
            self._ready.set()
            await server.wait_closed()

    # ------------------------------------------------------------------ HTTP endpoints
    def _json(self, connection, status: HTTPStatus, body: Any):
        resp = connection.respond(status, json.dumps(body))
        resp.headers["Content-Type"] = "application/json"
        return resp

    def _http(self, connection, request):
        url = urlparse(request.path)
        q = parse_qs(url.query)
        key = request.headers.get("xi-api-key")
        if url.path == "/v1/convai/conversation/get-signed-url":
            self.signed_url_keys.append(key)
            if key != self.cfg.api_key:
                return self._json(connection, HTTPStatus.UNAUTHORIZED, {"detail": "invalid api key"})
            agent = q.get("agent_id", [""])[0]
            return self._json(connection, HTTPStatus.OK, {
                "signed_url": f"ws://127.0.0.1:{self.port}/v1/convai/conversation?agent_id={agent}&token=tok"})
        if url.path.startswith("/v1/convai/conversations/"):
            self.record_gets += 1
            if key != self.cfg.api_key:
                return self._json(connection, HTTPStatus.UNAUTHORIZED, {"detail": "invalid api key"})
            return self._json(connection, HTTPStatus.OK, self._record(url.path.rsplit("/", 1)[1]))
        if url.path == "/v1/convai/conversation":
            if q.get("token", [""])[0] != "tok" and self.cfg.api_key:
                return connection.respond(HTTPStatus.FORBIDDEN, "no token")
            return None  # go on with the WebSocket
        return connection.respond(HTTPStatus.NOT_FOUND, "not found")

    def _record(self, conversation_id: str) -> dict[str, Any]:
        tool_calls, tool_results = [], []
        for i, w in enumerate(self.cfg.webhook_tools):
            rid = f"req_{i}"
            tool_calls.append({"request_id": rid, "tool_name": w["name"], "params_as_json": json.dumps(w["args"]),
                               "type": w.get("type", "webhook"), "tool_has_been_called": True})
            tool_results.append({"request_id": rid, "tool_name": w["name"], "result_value": json.dumps(w.get("result", {"ok": True})),
                                 "is_error": False, "type": w.get("type", "webhook")})
        transcript = [
            {"role": "user", "message": "hi", "time_in_call_secs": 1},
            {"role": "agent", "message": None, "time_in_call_secs": 2, "tool_calls": tool_calls, "tool_results": tool_results},
        ]
        return {"conversation_id": conversation_id, "agent_id": self.cfg.agent_id, "status": self.cfg.record_status,
                "transcript": transcript,
                "metadata": {"call_duration_secs": 9, "cost": 120, "cost_fiat": 0.0123, "termination_reason": "client disconnected"},
                "analysis": {"call_successful": "success"}}

    # ------------------------------------------------------------------ the conversation
    def _audio_msg(self, samples: np.ndarray, event_id: int) -> str:
        raw = encode_caller_audio(samples, parse_format(self.cfg.out_format))  # same encoder works both ways
        return json.dumps({"type": "audio", "audio_event": {"audio_base_64": base64.b64encode(raw).decode(), "event_id": event_id}})

    async def _handler(self, ws) -> None:
        cfg = self.cfg
        in_fmt = parse_format(cfg.in_format)
        turn = 0
        event = 0
        caller_loud = False
        last_loud = 0.0
        pending: asyncio.Task | None = None
        tool_answers: dict[str, asyncio.Future] = {}

        async def reply(index: int) -> None:
            nonlocal event
            await asyncio.sleep(cfg.reply_delay_ms / 1000)
            event += 1
            if index < len(cfg.heard):
                await ws.send(json.dumps({"type": "user_transcript",
                                          "user_transcription_event": {"user_transcript": cfg.heard[index], "event_id": event}}))
            if index in cfg.client_tools:
                name, params = cfg.client_tools[index]
                call_id = f"call_{index}"
                fut = asyncio.get_running_loop().create_future()
                tool_answers[call_id] = fut
                await ws.send(json.dumps({"type": "client_tool_call", "client_tool_call": {
                    "tool_name": name, "tool_call_id": call_id, "parameters": params, "event_id": event,
                    "expects_response": True}}))
                await asyncio.wait_for(fut, 5)
            text = cfg.replies[index] if index < len(cfg.replies) else f"reply {index}"
            await ws.send(json.dumps({"type": "agent_response", "agent_response_event": {"agent_response": text, "event_id": event}}))
            await ws.send(self._audio_msg(tone(cfg.reply_ms, freq=520), event))
            if cfg.end_after_turns is not None and index + 1 >= cfg.end_after_turns:
                await asyncio.sleep(cfg.reply_ms / 1000 + 0.2)
                await ws.close(1000, "end_call")

        try:
            async for msg in ws:
                data = json.loads(msg)
                if data.get("type") == "conversation_initiation_client_data":
                    self.client_data.append(data)
                    await ws.send(json.dumps({"type": "conversation_initiation_metadata",
                                              "conversation_initiation_metadata_event": {
                                                  "conversation_id": "conv_test_1",
                                                  "agent_output_audio_format": cfg.out_format,
                                                  "user_input_audio_format": cfg.in_format}}))
                    await ws.send(json.dumps({"type": "ping", "ping_event": {"event_id": 99, "ping_ms": 10}}))
                    if cfg.greet_ms:
                        await ws.send(json.dumps({"type": "agent_response",
                                                  "agent_response_event": {"agent_response": "Hello from ElevenLabs.", "event_id": 0}}))
                        await ws.send(self._audio_msg(tone(cfg.greet_ms, freq=600), 0))
                    continue
                if data.get("type") == "pong":
                    self.pongs.append(data)
                    continue
                if data.get("type") == "client_tool_result":
                    self.tool_results.append(data)
                    fut = tool_answers.get(data.get("tool_call_id"))
                    if fut and not fut.done():
                        fut.set_result(data)
                    continue
                if "user_audio_chunk" not in data:
                    continue
                samples = decode_agent_audio(base64.b64decode(data["user_audio_chunk"]), in_fmt)
                self.received_samples += len(samples)
                now = time.monotonic()
                if rms_dbfs(samples) > -40:
                    caller_loud = True
                    last_loud = now
                elif caller_loud and now - last_loud >= cfg.end_silence_ms / 1000:
                    caller_loud = False
                    if pending is None or pending.done():
                        pending = asyncio.create_task(reply(turn))
                    turn += 1
        except Exception:
            pass
