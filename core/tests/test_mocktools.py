from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from panchovoice.fixture import load_fixture
from panchovoice.mocktools import (MockToolSession, SessionRegistry, make_mock_router, normalize_day, normalize_time,
                                   start_mock_server, subset_match)


@pytest.fixture
def session(fixture_path: Path) -> MockToolSession:
    return MockToolSession(load_fixture(fixture_path))


@pytest.mark.parametrize("raw,out", [
    ("10", "10:00"), ("10:00", "10:00"), ("10 am", "10:00"), ("10 AM", "10:00"), ("10 a.m.", "10:00"),
    ("2 pm", "14:00"), ("2:30 PM", "14:30"), ("15:30", "15:30"), (" 9 ", "09:00"), ("12 am", "00:00"),
    ("12 pm", "12:00"), (10, "10:00"), ("noon", "noon"), ("10.00", "10:00"), ("a las 10", "10:00"),
])
def test_normalize_time(raw, out):
    assert normalize_time(raw) == out


@pytest.mark.parametrize("raw,out", [("Tuesday", "tuesday"), (" TUE ", "tuesday"), ("martes", "tuesday"),
                                     ("miércoles", "wednesday"), ("someday", "someday")])
def test_normalize_day(raw, out):
    assert normalize_day(raw) == out


def test_find_ok_and_not_found(session: MockToolSession):
    r = session.call("find_slot", {"day": "Tuesday", "time": "10 am"})
    assert r == {"ok": True, "result": {"day": "tuesday", "time": "10:00"}}
    assert session.call("find_slot", {"day": "friday", "time": "10"}) == {"ok": False, "error": "not_found"}


def test_find_fields_and_case(session: MockToolSession):
    r = session.call("check_balance", {"name": "  sam rivera "})
    assert r == {"ok": True, "result": {"name": "Sam Rivera", "balance": 120.5}}
    assert "phone" not in r["result"]


def test_find_all(session: MockToolSession):
    r = session.call("list_slots", {"day": "tuesday"})
    assert r["ok"] and len(r["result"]) == 3


def test_insert_requires_and_removes_slot(session: MockToolSession):
    r = session.call("book_appointment", {"name": "Sam Rivera", "day": "TUESDAY", "time": "10"})
    assert r == {"ok": True}
    state = session.get_state()
    assert state["appointments"] == [{"name": "Sam Rivera", "day": "tuesday", "time": "10:00"}]
    assert {"day": "tuesday", "time": "10:00"} not in state["slots"]
    # the slot is gone now
    again = session.call("book_appointment", {"name": "Jordan Lee", "day": "tuesday", "time": "10:00"})
    assert again == {"ok": False, "error": "slot_not_available"}


def test_delete(session: MockToolSession):
    session.call("book_appointment", {"name": "Sam Rivera", "day": "tuesday", "time": "11:00"})
    assert session.call("cancel_appointment", {"name": "sam rivera", "day": "Tuesday"}) == {"ok": True, "deleted": 1}
    assert session.call("cancel_appointment", {"name": "sam rivera", "day": "Tuesday"}) == {"ok": True, "deleted": 0}
    assert session.get_state()["appointments"] == []


def test_missing_params_and_unknown_tool(session: MockToolSession):
    assert session.call("find_slot", {"day": "tuesday"})["error"] == "missing_params"
    assert session.call("teleport", {}) == {"ok": False, "error": "unknown_tool"}


def test_calls_recorded_with_t_ms(session: MockToolSession):
    session.start_clock(time.monotonic() - 1.5)
    session.call("find_slot", {"day": "tuesday", "time": "10:00"})
    calls = session.get_calls()
    assert len(calls) == 1
    c = calls[0]
    assert c["name"] == "find_slot" and c["args"] == {"day": "tuesday", "time": "10:00"}
    assert c["response"]["ok"] is True
    assert 1450 <= c["t_ms"] <= 2500


def test_fixture_state_not_mutated(fixture_path: Path):
    fx = load_fixture(fixture_path)
    s = MockToolSession(fx)
    s.call("book_appointment", {"name": "Sam Rivera", "day": "tuesday", "time": "10:00"})
    assert fx.state["appointments"] == []
    assert MockToolSession(fx).get_state()["appointments"] == []


def test_subset_match_normalizes():
    assert subset_match({"day": "Tuesday", "time": "10 AM", "name": "sam rivera"},
                        {"day": "tuesday", "time": "10:00", "name": "Sam Rivera", "extra": 1})
    assert not subset_match({"time": "11:00"}, {"time": "10:00"})
    assert subset_match({"balance": "120.5"}, {"balance": 120.5})
    assert not subset_match({"missing": 1}, {})


def test_router_with_testclient(fixture_path: Path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    reg = SessionRegistry()
    s = reg.create(load_fixture(fixture_path), "abc")
    app = FastAPI()
    app.include_router(make_mock_router(reg))
    c = TestClient(app)
    tools = c.get("/mock/abc/tools").json()
    assert {"name", "description", "params"} == set(tools[0])
    r = c.post("/mock/abc/tools/find_slot", json={"day": "tuesday", "time": "10"})
    assert r.status_code == 200 and r.json()["ok"] is True
    r = c.post("/mock/abc/tools/find_slot", json={"day": "friday", "time": "10"})
    assert r.status_code == 200 and r.json() == {"ok": False, "error": "not_found"}
    assert c.get("/mock/abc/state").json()["appointments"] == []
    assert len(c.get("/mock/abc/calls").json()) == 2
    assert c.get("/mock/nope/state").status_code == 404
    assert s.get_calls()[0]["name"] == "find_slot"


def test_standalone_server_thread(fixture_path: Path):
    reg = SessionRegistry()
    srv = start_mock_server(0 if False else 8799, reg)
    try:
        s = reg.create(load_fixture(fixture_path))
        r = httpx.post(f"{srv.base_url}/{s.session_id}/tools/check_balance", json={"name": "Jordan Lee"}, timeout=5)
        assert r.json() == {"ok": True, "result": {"name": "Jordan Lee", "balance": 0}}
        assert len(s.get_calls()) == 1
    finally:
        srv.stop()
