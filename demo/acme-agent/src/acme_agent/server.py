"""WebSocket server: ws://host:port/ws speaks pv-ws/1. GET /health answers plain HTTP."""

from __future__ import annotations

import asyncio
import itertools
import logging
from http import HTTPStatus

import httpx
from websockets.asyncio.server import serve

from .call import Call, Settings
from .ears import Transcriber

log = logging.getLogger("acme_agent")


async def serve_forever(host: str, port: int, settings: Settings, transcriber: Transcriber) -> None:
    ids = itertools.count(1)
    async with httpx.AsyncClient(limits=httpx.Limits(max_keepalive_connections=10)) as http:

        def process_request(connection, request):
            if request.path == "/health":
                return connection.respond(HTTPStatus.OK, "ok\n")
            if request.path != "/ws":
                return connection.respond(HTTPStatus.NOT_FOUND, "pv-ws/1 endpoint is /ws\n")
            return None

        async def handler(ws) -> None:
            call = Call(ws, settings, transcriber, http, next(ids))
            try:
                await call.run()
            except Exception:  # one broken call must never take the server down
                log.exception("[call %d] crashed", call.id)

        # Warm the LLM in the background so the first turn does not pay the model load.
        from .brain import Brain

        async def warm() -> None:
            try:
                await Brain(http, settings.ollama_url, settings.model).warm_up()
                log.info("LLM %s is loaded", settings.model)
            except httpx.HTTPError as e:
                log.warning("could not warm up Ollama at %s: %s", settings.ollama_url, e)

        warm_task = asyncio.create_task(warm())
        async with serve(handler, host, port, process_request=process_request, max_size=2**22) as server:
            log.info("ACME Appointments listening on ws://%s:%d/ws (bug %s)", host, port, "ON" if settings.bug else "off")
            await server.serve_forever()
        warm_task.cancel()
