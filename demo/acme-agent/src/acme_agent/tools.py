"""Tools: read the tool list from the PanchoVoiceAI mock tool API and call tools over HTTP."""

from __future__ import annotations

import httpx

_JSON_TYPES = {"string": "string", "number": "number", "integer": "integer", "boolean": "boolean"}


def to_ollama_tools(specs: list[dict]) -> list[dict]:
    """Convert contract tool specs [{name, description, params: {p: "string"}}] to Ollama tool format."""
    tools = []
    for spec in specs:
        params = spec.get("params") or {}
        props = {p: {"type": _JSON_TYPES.get(str(t), "string")} for p, t in params.items()}
        for p in props:
            if p == "day":
                props[p]["description"] = "weekday in lowercase English, for example tuesday"
            elif p == "time":
                props[p]["description"] = "24 hour time HH:MM, for example 10:00"
            elif p == "name":
                props[p]["description"] = "full name of the customer"
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": spec["name"],
                    "description": spec.get("description", ""),
                    "parameters": {"type": "object", "properties": props, "required": list(props)},
                },
            }
        )
    return tools


class MockTools:
    """Client for `GET {base}/tools` and `POST {base}/tools/{name}` (contract section 4)."""

    def __init__(self, client: httpx.AsyncClient, base_url: str | None) -> None:
        self.client = client
        self.base = (base_url or "").rstrip("/")
        self.specs: list[dict] = []

    async def load(self) -> list[dict]:
        if not self.base:
            self.specs = []
            return self.specs
        resp = await self.client.get(f"{self.base}/tools", timeout=10)
        resp.raise_for_status()
        self.specs = resp.json()
        return self.specs

    @property
    def names(self) -> set[str]:
        return {s["name"] for s in self.specs}

    def ollama_tools(self) -> list[dict]:
        return to_ollama_tools(self.specs)

    async def call(self, name: str, args: dict) -> dict:
        if name not in self.names:
            return {"ok": False, "error": "unknown_tool"}
        try:
            resp = await self.client.post(f"{self.base}/tools/{name}", json=args, timeout=10)
            return resp.json()
        except (httpx.HTTPError, ValueError) as e:
            return {"ok": False, "error": f"tool_unreachable: {type(e).__name__}"}
