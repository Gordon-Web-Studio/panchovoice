from __future__ import annotations

import xml.etree.ElementTree as ET

from panchovoice.checks import (check_barge_in, check_final_state, check_judge, check_latency, check_phrases,
                                check_tools, check_wer, gate, run_status)
from panchovoice.config import GateConfig
from panchovoice.judge import JudgeAnswer
from panchovoice.report import junit_xml
from panchovoice.scenario import Expect

CALLS = [
    {"t_ms": 3000, "name": "find_slot", "args": {"day": "Tuesday", "time": "10 am"}, "response": {"ok": True}},
    {"t_ms": 9000, "name": "book_appointment", "args": {"name": "sam rivera", "day": "tuesday", "time": "10:00"},
     "response": {"ok": True}},
]


def _by_id(checks):
    return {c["id"]: c for c in checks}


def test_tool_checks_pass_with_normalization():
    e = Expect.model_validate({
        "tool_calls": [{"name": "find_slot", "args": {"day": "tuesday", "time": "10:00"}},
                       {"name": "book_appointment", "args": {"name": "Sam Rivera"}}],
        "forbidden_tools": ["cancel_appointment"], "order": ["find_slot", "book_appointment"], "no_extra_tools": True,
    })
    c = _by_id(check_tools(e, CALLS))
    assert all(x["status"] == "passed" for x in c.values()), c
    assert c["tool_calls"]["detail"] == "2 of 2 expected calls"


def test_tool_checks_fail():
    e = Expect.model_validate({
        "tool_calls": [{"name": "book_appointment", "args": {"time": "11:00"}}],
        "forbidden_tools": ["book_appointment"], "order": ["book_appointment", "find_slot"], "no_extra_tools": True,
    })
    c = _by_id(check_tools(e, CALLS))
    assert c["tool_calls"]["status"] == "failed" and "0 of 1" in c["tool_calls"]["detail"]
    assert c["forbidden_tools"]["passed"] is False
    assert c["order"]["passed"] is False
    assert c["no_extra_tools"]["passed"] is False and "find_slot" in c["no_extra_tools"]["detail"]


def test_final_state():
    state = {"appointments": [{"name": "Sam Rivera", "day": "tuesday", "time": "10:00"}]}
    ok = Expect.model_validate({"final_state": {"appointments": [{"name": "sam rivera", "time": "10 AM"}]}})
    assert check_final_state(ok, state)[0]["passed"] is True
    empty = Expect.model_validate({"final_state": {"appointments": []}})
    assert check_final_state(empty, state)[0]["passed"] is False
    assert check_final_state(empty, {"appointments": []})[0]["passed"] is True


def test_latency_wer_barge_phrases():
    e = Expect.model_validate({"latency_ms": {"p50": 1000, "p95": 2000}, "wer_max": 0.1,
                               "barge_in": {"max_stop_ms": 800}, "must_say": ["booked"], "never_say": ["system prompt"]})
    assert check_latency(e, {"latency_p50_ms": 900, "latency_p95_ms": 2100})[0]["passed"] is False
    assert check_latency(e, {"latency_p50_ms": 900, "latency_p95_ms": 1500})[0]["passed"] is True
    assert check_latency(e, {"latency_p50_ms": None})[0]["passed"] is False
    assert check_wer(e, {"wer": 0.05})[0]["passed"] is True
    assert check_wer(e, {"wer": None})[0]["status"] == "error"
    turns = [{"_planned_interrupt": True, "stop_ms": 600}]
    assert check_barge_in(e, turns, True)[0]["passed"] is True
    assert check_barge_in(e, [{"_planned_interrupt": True, "stop_ms": None}], True)[0]["status"] == "error"
    assert check_barge_in(e, turns, False) == []
    p = _by_id(check_phrases(e, "Okay, you are BOOKED. I cannot share my System Prompt."))
    assert p["must_say_1"]["passed"] is True
    assert p["never_say_1"]["passed"] is False and "System Prompt" in p["never_say_1"]["evidence"]


def test_judge_checks_and_status():
    answers = [JudgeAnswer("q1", True, "Agent: hi"), JudgeAnswer("q2", False, "Agent: done"),
               JudgeAnswer("q3", None, "", error="judge unavailable: down")]
    c = check_judge(answers)
    assert [x["status"] for x in c] == ["passed", "failed", "error"]
    assert c[1]["evidence"] == "Agent: done"
    assert run_status(c) == "failed"
    assert run_status([c[0], c[2]]) == "error"
    assert run_status([c[0]]) == "passed"


def _runs(spec):
    return [{"scenario_id": s, "status": st, "risk": r} for s, st, r in spec]


def test_gate_rule():
    cfg = GateConfig(pass_rate=0.95, critical_must_pass_all=True)
    g = gate(_runs([("a", "passed", "critical")] * 20), cfg)
    assert g["gate_passed"] and g["pass_rate"] == 1.0
    # one normal failure in 20: 95% passes the rate
    g = gate(_runs([("a", "passed", "normal")] * 19 + [("b", "failed", "normal")]), cfg)
    assert g["pass_rate"] == 0.95 and g["gate_passed"]
    # a critical failure fails the gate even with a high rate
    g = gate(_runs([("a", "passed", "normal")] * 30 + [("c", "failed", "critical")]), cfg)
    assert not g["gate_passed"] and "critical" in g["gate_detail"]
    # errors are excluded from the rate but fail the gate
    g = gate(_runs([("a", "passed", "normal")] * 3 + [("b", "error", "normal")]), cfg)
    assert g["pass_rate"] == 1.0 and g["errors"] == 1 and not g["gate_passed"]
    # critical failures allowed when the flag is off
    g = gate(_runs([("a", "passed", "normal")] * 30 + [("c", "failed", "critical")]),
             GateConfig(pass_rate=0.9, critical_must_pass_all=False))
    assert g["gate_passed"]
    assert gate([], cfg)["pass_rate"] is None and not gate([], cfg)["gate_passed"]


def test_junit_xml():
    suite = {"suite_id": "s_1", "started_at": "2026-11-18T03:15:00Z", "pass_rate": 0.5, "gate_passed": False, "gate_detail": "x"}
    results = [
        {"run_id": "r1", "scenario_id": "a", "agent_id": "acme-demo", "status": "passed", "duration_ms": 1000, "checks": [], "metrics": {}},
        {"run_id": "r2", "scenario_id": "b", "agent_id": "acme-demo", "status": "failed", "duration_ms": 2000, "metrics": {},
         "checks": [{"id": "judge_1", "status": "failed", "detail": "booked without confirming", "evidence": "Agent: done"}]},
        {"run_id": "r3", "scenario_id": "b", "agent_id": "acme-demo", "status": "error", "duration_ms": 0, "checks": [],
         "metrics": {}, "error": "agent down"},
    ]
    xml = junit_xml(suite, results)
    root = ET.fromstring(xml)
    s = root.find("testsuite")
    assert s.get("tests") == "3" and s.get("failures") == "1" and s.get("errors") == "1"
    cases = s.findall("testcase")
    assert [c.get("name") for c in cases] == ["a", "b", "b #2"]
    assert "booked without confirming" in cases[1].find("failure").get("message")
    assert cases[2].find("error").get("message") == "agent down"
