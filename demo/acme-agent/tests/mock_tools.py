"""A tiny stand-alone mock tool server (contract sections 3 and 4), stdlib only.

Used by the self test so the demo agent can be checked without the core package.
"""

from __future__ import annotations

import copy
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURE_PATH = Path(__file__).resolve().parents[3] / "fixtures" / "acme-clinic.json"

# Inline copy of the contract example fixture, used when fixtures/acme-clinic.json is missing.
INLINE_FIXTURE = {
    "id": "acme-clinic",
    "description": "ACME clinic with open slots and two customers",
    "tools": [
        {"name": "find_slot", "description": "Check if a slot is free", "params": {"day": "string", "time": "string"},
         "action": "find", "collection": "slots", "match": ["day", "time"], "returns": "first_or_error"},
        {"name": "book_appointment", "description": "Book a slot for a customer",
         "params": {"name": "string", "day": "string", "time": "string"},
         "action": "insert", "collection": "appointments", "requires": {"collection": "slots", "match": ["day", "time"]}},
        {"name": "check_balance", "description": "Get a customer balance", "params": {"name": "string"},
         "action": "find", "collection": "customers", "match": ["name"], "returns": "first_or_error", "fields": ["name", "balance"]},
        {"name": "cancel_appointment", "description": "Cancel an appointment", "params": {"name": "string", "day": "string"},
         "action": "delete", "collection": "appointments", "match": ["name", "day"]},
    ],
    "state": {
        "slots": [{"day": "tuesday", "time": "10:00"}, {"day": "tuesday", "time": "11:00"}, {"day": "wednesday", "time": "09:00"}],
        "customers": [{"name": "Sam Rivera", "phone": "555-0142", "balance": 120.5},
                      {"name": "Jordan Lee", "phone": "555-0199", "balance": 0}],
        "appointments": [],
    },
}

_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def load_fixture() -> dict:
    if FIXTURE_PATH.exists():
        return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return copy.deepcopy(INLINE_FIXTURE)


def normalize_time(value: str) -> str:
    v = str(value).strip().lower().replace(".", "")
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", v)
    if not m:
        return v
    h, mins, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ampm == "pm" and h < 12:
        h += 12
    if ampm == "am" and h == 12:
        h = 0
    return f"{h:02d}:{mins:02d}"


def normalize(param: str, value) -> object:
    if not isinstance(value, str):
        return value
    if param == "time":
        return normalize_time(value)
    v = value.strip().lower()
    if param == "day":
        for d in _DAYS:
            if v.startswith(d[:3]):
                return d
    return v


class MockBackend:
    def __init__(self, fixture: dict) -> None:
        self.fixture = fixture
        self.tools = {t["name"]: t for t in fixture["tools"]}
        self.state = copy.deepcopy(fixture["state"])
        self.calls: list[dict] = []
        self.t0 = time.monotonic()
        self.lock = threading.Lock()

    def _matches(self, row: dict, args: dict, keys: list[str]) -> bool:
        return all(normalize(k, row.get(k)) == normalize(k, args.get(k)) for k in keys)

    def call(self, name: str, args: dict) -> dict:
        with self.lock:
            resp = self._call(name, args)
            self.calls.append({"t_ms": int((time.monotonic() - self.t0) * 1000), "name": name, "args": args, "response": resp})
            return resp

    def _call(self, name: str, args: dict) -> dict:
        tool = self.tools.get(name)
        if tool is None:
            return {"ok": False, "error": "unknown_tool"}
        rows = self.state.setdefault(tool["collection"], [])
        action = tool["action"]
        if action == "find":
            for row in rows:
                if self._matches(row, args, tool["match"]):
                    fields = tool.get("fields")
                    return {"ok": True, "result": {k: row[k] for k in fields} if fields else row}
            return {"ok": False, "error": "not_found"}
        if action == "insert":
            req = tool.get("requires")
            matched = None
            if req:
                src = self.state.setdefault(req["collection"], [])
                matched = next((r for r in src if self._matches(r, args, req["match"])), None)
                if matched is None:
                    return {"ok": False, "error": "slot_not_available"}
            row = {k: normalize(k, v) if k in ("day", "time") else v for k, v in args.items()}
            rows.append(row)
            if matched is not None and tool["collection"] == "appointments" and req["collection"] == "slots":
                self.state["slots"].remove(matched)
            return {"ok": True}
        if action == "delete":
            keep = [r for r in rows if not self._matches(r, args, tool["match"])]
            n = len(rows) - len(keep)
            self.state[tool["collection"]] = keep
            return {"ok": True, "deleted": n}
        return {"ok": False, "error": "bad_action"}


def start_server(backend: MockBackend, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """Start the HTTP API on a free port. Returns (server, mock_tools_url for session 'selftest')."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:  # keep test output clean
            pass

        def _send(self, obj, code: int = 200) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parts = self.path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "mock":
                if parts[2] == "tools":
                    return self._send([{k: t[k] for k in ("name", "description", "params")} for t in backend.fixture["tools"]])
                if parts[2] == "state":
                    return self._send(backend.state)
                if parts[2] == "calls":
                    return self._send(backend.calls)
            self._send({"error": "not_found"}, 404)

        def do_POST(self) -> None:
            parts = self.path.strip("/").split("/")
            if len(parts) == 4 and parts[0] == "mock" and parts[2] == "tools":
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    args = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    args = {}
                return self._send(backend.call(parts[3], args if isinstance(args, dict) else {}))
            self._send({"error": "not_found"}, 404)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/mock/selftest"
