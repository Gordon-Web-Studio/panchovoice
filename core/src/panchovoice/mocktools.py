"""Mock tools backend (contract sections 3 and 4).

`MockToolSession` keeps the state of one call and records every tool call.
`make_mock_router` is a FastAPI APIRouter (prefix `/mock`) that the API server can mount.
`start_mock_server` runs that router alone in a background thread for `pv run`.
"""

from __future__ import annotations

import copy
import re
import secrets
import socket
import threading
import time
from typing import Any

from .fixture import Fixture, ToolDef

# --------------------------------------------------------------------------- normalization

_DAYS = {
    "monday": "monday", "mon": "monday", "lunes": "monday",
    "tuesday": "tuesday", "tue": "tuesday", "tues": "tuesday", "martes": "tuesday",
    "wednesday": "wednesday", "wed": "wednesday", "miercoles": "wednesday", "miércoles": "wednesday",
    "thursday": "thursday", "thu": "thursday", "thur": "thursday", "thurs": "thursday", "jueves": "thursday",
    "friday": "friday", "fri": "friday", "viernes": "friday",
    "saturday": "saturday", "sat": "saturday", "sabado": "saturday", "sábado": "saturday",
    "sunday": "sunday", "sun": "sunday", "domingo": "sunday",
}

_TIME_RE = re.compile(
    r"^(?:at\s+|a\s+las\s+|las\s+)?(\d{1,2})(?:[:.h](\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?|h|hs|o'?clock)?$"
)


def normalize_time(value: Any) -> Any:
    """`10`, `10:00`, `10 am`, `10.00`, `2 pm` -> `10:00`, `14:00`. Other values pass through (stripped, lowercase)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        h = int(value)
        return f"{h:02d}:00" if 0 <= h <= 23 else value
    if not isinstance(value, str):
        return value
    s = value.strip().lower()
    m = _TIME_RE.match(s)
    if not m:
        return s
    h = int(m.group(1))
    mi = int(m.group(2) or 0)
    suffix = (m.group(3) or "").replace(".", "").replace(" ", "")
    if suffix.startswith("p") and h < 12:
        h += 12
    if suffix.startswith("a") and h == 12:
        h = 0
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return s
    return f"{h:02d}:{mi:02d}"


def normalize_day(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    s = value.strip().lower().rstrip(".")
    return _DAYS.get(s, s)


def normalize_value(key: str, value: Any) -> Any:
    """Canonical form used for matching and for stored rows."""
    if key == "time":
        return normalize_time(value)
    if key == "day":
        return normalize_day(value)
    if isinstance(value, str):
        return value.strip()
    return value


def match_key(key: str, value: Any) -> Any:
    """Comparison form: canonical value, then case folded for strings."""
    v = normalize_value(key, value)
    if isinstance(v, str):
        return " ".join(v.lower().split())
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def values_equal(key: str, a: Any, b: Any) -> bool:
    ka, kb = match_key(key, a), match_key(key, b)
    if ka == kb:
        return True
    # numbers given as strings ("120.5" vs 120.5)
    try:
        return float(ka) == float(kb)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def subset_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    """Every key in `expected` is in `actual` with an equal value (with normalization)."""
    for k, v in expected.items():
        if k not in actual:
            return False
        if isinstance(v, dict) and isinstance(actual[k], dict):
            if not subset_match(v, actual[k]):
                return False
        elif not values_equal(k, v, actual[k]):
            return False
    return True


# --------------------------------------------------------------------------- session


class MockToolSession:
    """State and call log of one call. Thread safe (the HTTP server runs in another thread)."""

    def __init__(self, fixture: Fixture, session_id: str | None = None, t0: float | None = None):
        self.fixture = fixture
        self.session_id = session_id or ("m_" + secrets.token_hex(6))
        self.state: dict[str, list[dict[str, Any]]] = copy.deepcopy(fixture.state)
        self.calls: list[dict[str, Any]] = []
        self.t0 = t0 if t0 is not None else time.monotonic()
        self._lock = threading.Lock()

    # clock ------------------------------------------------------------------
    def start_clock(self, t0: float | None = None) -> None:
        """Set t0 (time.monotonic seconds). The runner calls this when the caller connects."""
        with self._lock:
            self.t0 = t0 if t0 is not None else time.monotonic()

    def now_ms(self) -> int:
        return int(round((time.monotonic() - self.t0) * 1000))

    # api --------------------------------------------------------------------
    def tools(self) -> list[dict[str, Any]]:
        return [t.public() for t in self.fixture.tools]

    def get_state(self) -> dict[str, list[dict[str, Any]]]:
        with self._lock:
            return copy.deepcopy(self.state)

    def get_calls(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self.calls)

    def call(self, name: str, args: dict[str, Any] | None) -> dict[str, Any]:
        args = dict(args or {})
        with self._lock:
            t_ms = int(round((time.monotonic() - self.t0) * 1000))
            tool = self.fixture.tool(name)
            if tool is None:
                response: dict[str, Any] = {"ok": False, "error": "unknown_tool"}
            else:
                response = self._run(tool, args)
            self.calls.append({"t_ms": t_ms, "name": name, "args": copy.deepcopy(args), "response": copy.deepcopy(response)})
            return copy.deepcopy(response)

    # internals --------------------------------------------------------------
    def _rows_matching(self, collection: str, keys: list[str], args: dict[str, Any]) -> list[int]:
        rows = self.state.get(collection, [])
        out = []
        for i, row in enumerate(rows):
            if all(k in row and values_equal(k, args.get(k), row[k]) for k in keys):
                out.append(i)
        return out

    def _run(self, tool: ToolDef, args: dict[str, Any]) -> dict[str, Any]:
        needed = set(tool.match) | (set(tool.requires.match) if tool.requires else set())
        if tool.action == "insert":
            needed |= set(tool.params)
        missing = sorted(k for k in needed if args.get(k) in (None, ""))
        if missing:
            return {"ok": False, "error": "missing_params", "params": missing}

        if tool.action == "find":
            idx = self._rows_matching(tool.collection, tool.match, args)
            if not idx:
                return {"ok": False, "error": "not_found"}
            rows = [self.state[tool.collection][i] for i in idx]
            if tool.fields:
                rows = [{k: r[k] for k in tool.fields if k in r} for r in rows]
            if tool.returns == "all":
                return {"ok": True, "result": copy.deepcopy(rows)}
            return {"ok": True, "result": copy.deepcopy(rows[0])}

        if tool.action == "insert":
            matched: list[int] = []
            if tool.requires:
                matched = self._rows_matching(tool.requires.collection, tool.requires.match, args)
                if not matched:
                    return {"ok": False, "error": "slot_not_available"}
            row = {k: normalize_value(k, v) for k, v in args.items() if k in tool.params}
            self.state.setdefault(tool.collection, []).append(row)
            if tool.requires and tool.requires.collection == "slots" and matched:
                del self.state["slots"][matched[0]]
            return {"ok": True}

        if tool.action == "delete":
            idx = self._rows_matching(tool.collection, tool.match, args)
            for i in reversed(idx):
                del self.state[tool.collection][i]
            return {"ok": True, "deleted": len(idx)}

        return {"ok": False, "error": "unsupported_action"}


class SessionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, MockToolSession] = {}
        self._lock = threading.Lock()

    def create(self, fixture: Fixture, session_id: str | None = None) -> MockToolSession:
        s = MockToolSession(fixture, session_id)
        with self._lock:
            self._sessions[s.session_id] = s
        return s

    def add(self, session: MockToolSession) -> None:
        with self._lock:
            self._sessions[session.session_id] = session

    def get(self, session_id: str) -> MockToolSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def remove(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def ids(self) -> list[str]:
        with self._lock:
            return list(self._sessions)


DEFAULT_REGISTRY = SessionRegistry()


# --------------------------------------------------------------------------- HTTP router


def make_mock_router(registry: SessionRegistry | None = None):
    """FastAPI router with the contract section 4 endpoints under `/mock`."""
    from fastapi import APIRouter, Body, HTTPException

    reg = registry or DEFAULT_REGISTRY
    router = APIRouter(prefix="/mock", tags=["mock"])

    def _session(session: str) -> MockToolSession:
        s = reg.get(session)
        if s is None:
            raise HTTPException(status_code=404, detail=f"unknown session {session}")
        return s

    @router.get("/{session}/tools")
    def list_tools(session: str) -> list[dict[str, Any]]:
        return _session(session).tools()

    @router.post("/{session}/tools/{name}")
    def call_tool(session: str, name: str, args: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
        return _session(session).call(name, args or {})

    @router.get("/{session}/state")
    def get_state(session: str) -> dict[str, Any]:
        return _session(session).get_state()

    @router.get("/{session}/calls")
    def get_calls(session: str) -> list[dict[str, Any]]:
        return _session(session).get_calls()

    return router


def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
        return True


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MockServer:
    """The mock router alone, served by uvicorn in a daemon thread."""

    def __init__(self, registry: SessionRegistry, host: str, port: int):
        import uvicorn
        from fastapi import FastAPI

        self.registry = registry
        self.host = host
        self.port = port
        app = FastAPI(title="PanchoVoiceAI mock tools")
        app.include_router(make_mock_router(registry))
        cfg = uvicorn.Config(app, host=host, port=port, log_level="warning", lifespan="off")
        self._server = uvicorn.Server(cfg)
        self._thread = threading.Thread(target=self._server.run, name="pv-mock-server", daemon=True)

    @property
    def base_url(self) -> str:
        # 127.0.0.1, not localhost: on Windows a client that tries ::1 first can wait about 2 s per request
        return f"http://127.0.0.1:{self.port}/mock"

    def start(self, timeout: float = 10.0) -> "MockServer":
        self._thread.start()
        deadline = time.monotonic() + timeout
        while not self._server.started:
            if not self._thread.is_alive():
                raise RuntimeError(f"mock server could not start on port {self.port}")
            if time.monotonic() > deadline:
                raise RuntimeError("mock server start timed out")
            time.sleep(0.02)
        return self

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


def start_mock_server(port: int = 8700, registry: SessionRegistry | None = None, host: str = "127.0.0.1",
                      fallback_free_port: bool = True) -> MockServer:
    """Start the standalone mock server. If `port` is taken, use a free port (when allowed)."""
    reg = registry or DEFAULT_REGISTRY
    if not port_is_free(port, host):
        if not fallback_free_port:
            raise RuntimeError(f"port {port} is in use")
        port = _free_port()
    return MockServer(reg, host, port).start()
