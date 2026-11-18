"""The `expect` checks of a scenario (contract section 2). Pure functions over the run data.

Each check: {id, kind, status: passed|failed|error, passed: bool|None, detail, evidence?}.
`error` means the harness could not measure it (judge down, no transcript, barge-in not exercised).
"""

from __future__ import annotations

from typing import Any

from .config import GateConfig
from .mocktools import subset_match
from .scenario import Expect


def _check(cid: str, kind: str, ok: bool | None, detail: str, evidence: str | None = None) -> dict[str, Any]:
    status = "error" if ok is None else ("passed" if ok else "failed")
    c: dict[str, Any] = {"id": cid, "kind": kind, "passed": ok, "status": status, "detail": detail}
    if evidence is not None:
        c["evidence"] = evidence
    return c


def _fmt_args(args: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in args.items())


def is_subsequence(wanted: list[str], seen: list[str]) -> bool:
    it = iter(seen)
    return all(any(s == w for s in it) for w in wanted)


def check_tools(expect: Expect, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    names = [c["name"] for c in calls]
    if expect.tool_calls:
        missing = []
        for tc in expect.tool_calls:
            if not any(c["name"] == tc.name and subset_match(tc.args, c.get("args") or {}) for c in calls):
                missing.append(f"{tc.name}({_fmt_args(tc.args)})")
        n = len(expect.tool_calls)
        detail = f"{n - len(missing)} of {n} expected calls"
        if missing:
            seen = "; ".join(f"{c['name']}({_fmt_args(c.get('args') or {})})" for c in calls) or "none"
            detail += f". Missing: {', '.join(missing)}. Seen: {seen}"
        out.append(_check("tool_calls", "tools", not missing, detail))
    if expect.forbidden_tools:
        bad = sorted({n for n in names if n in expect.forbidden_tools})
        out.append(_check("forbidden_tools", "tools", not bad,
                          f"forbidden tools called: {', '.join(bad)}" if bad else "no forbidden tool was called"))
    if expect.no_extra_tools:
        allowed = {tc.name for tc in expect.tool_calls}
        extra = sorted({n for n in names if n not in allowed})
        out.append(_check("no_extra_tools", "tools", not extra,
                          f"unexpected tools called: {', '.join(extra)}" if extra else "only expected tools were called"))
    if expect.order:
        ok = is_subsequence(expect.order, names)
        out.append(_check("order", "tools", ok, f"expected order {' > '.join(expect.order)}; seen {' > '.join(names) or 'none'}"))
    return out


def check_final_state(expect: Expect, state: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    if expect.final_state is None:
        return []
    problems = []
    for coll, rows in expect.final_state.items():
        actual = state.get(coll, [])
        if not rows:
            if actual:
                problems.append(f"{coll} should be empty but has {len(actual)} rows")
            continue
        for row in rows:
            if not any(subset_match(row, a) for a in actual):
                problems.append(f"{coll} has no row matching {row}")
    return [_check("final_state", "state", not problems, "; ".join(problems) if problems else "final state matches")]


def check_latency(expect: Expect, metrics: dict[str, Any]) -> list[dict[str, Any]]:
    lat = expect.latency_ms
    if lat is None or (lat.p50 is None and lat.p95 is None):
        return []
    p50, p95 = metrics.get("latency_p50_ms"), metrics.get("latency_p95_ms")
    if p50 is None:
        return [_check("latency", "latency", False, "no agent reply was detected, latency not measurable")]
    bad = []
    if lat.p50 is not None and p50 > lat.p50:
        bad.append(f"p50 {p50} ms > {lat.p50} ms")
    if lat.p95 is not None and p95 is not None and p95 > lat.p95:
        bad.append(f"p95 {p95} ms > {lat.p95} ms")
    detail = "; ".join(bad) if bad else f"p50 {p50} ms, p95 {p95} ms within limits"
    return [_check("latency", "latency", not bad, detail)]


def check_barge_in(expect: Expect, turns: list[dict[str, Any]], has_interrupts: bool) -> list[dict[str, Any]]:
    if expect.barge_in is None or not has_interrupts:
        return []
    planned = [t for t in turns if t.get("_planned_interrupt")]
    measured = [t["stop_ms"] for t in planned if t.get("stop_ms") is not None]
    if not measured:
        return [_check("barge_in", "barge_in", None,
                       "barge-in not exercised: the agent was not speaking when the caller interrupted")]
    worst = max(measured)
    ok = worst <= expect.barge_in.max_stop_ms
    return [_check("barge_in", "barge_in", ok, f"agent stopped {worst} ms after the caller started (max {expect.barge_in.max_stop_ms} ms)")]


def check_wer(expect: Expect, metrics: dict[str, Any]) -> list[dict[str, Any]]:
    if expect.wer_max is None:
        return []
    w = metrics.get("wer")
    if w is None:
        return [_check("wer", "wer", None, "not measurable: the agent sent no transcript role=user messages")]
    return [_check("wer", "wer", w <= expect.wer_max, f"WER {w:.3f} (max {expect.wer_max})")]


def _find_quote(text: str, needle: str, width: int = 60) -> str:
    i = text.lower().find(needle.lower())
    if i < 0:
        return ""
    lo, hi = max(0, i - width // 2), min(len(text), i + len(needle) + width // 2)
    return text[lo:hi].strip()


def check_phrases(expect: Expect, agent_text: str) -> list[dict[str, Any]]:
    out = []
    low = agent_text.lower()
    for i, phrase in enumerate(expect.must_say):
        ok = phrase.lower() in low
        out.append(_check(f"must_say_{i + 1}", "must_say", ok,
                          f"agent said '{phrase}'" if ok else f"agent never said '{phrase}'",
                          _find_quote(agent_text, phrase) if ok else None))
    for i, phrase in enumerate(expect.never_say):
        hit = phrase.lower() in low
        out.append(_check(f"never_say_{i + 1}", "never_say", not hit,
                          f"agent said the forbidden phrase '{phrase}'" if hit else f"agent never said '{phrase}'",
                          _find_quote(agent_text, phrase) if hit else None))
    return out


def check_judge(answers: list[Any]) -> list[dict[str, Any]]:
    out = []
    for i, a in enumerate(answers):
        if a.error:
            out.append(_check(f"judge_{i + 1}", "judge", None, f"{a.question} ({a.error})"))
            continue
        detail = a.question
        if not a.evidence_found:
            detail += " (note: the evidence quote was not found word for word in the transcript)"
        out.append(_check(f"judge_{i + 1}", "judge", a.passed, detail, a.evidence))
    return out


def run_status(checks: list[dict[str, Any]]) -> str:
    if any(c["status"] == "failed" for c in checks):
        return "failed"
    if any(c["status"] == "error" for c in checks):
        return "error"
    return "passed"


# --------------------------------------------------------------------------- gate (contract section 8)


def gate(runs: list[dict[str, Any]], cfg: GateConfig) -> dict[str, Any]:
    """runs: [{status, risk, scenario_id}]. Returns {pass_rate, gate_passed, detail, passed, failed, errors}."""
    passed = sum(1 for r in runs if r["status"] == "passed")
    failed = sum(1 for r in runs if r["status"] == "failed")
    errors = sum(1 for r in runs if r["status"] == "error")
    pass_rate = passed / (passed + failed) if (passed + failed) else None
    reasons = []
    if pass_rate is None:
        reasons.append("no run produced a pass or fail")
    elif pass_rate < cfg.pass_rate:
        reasons.append(f"pass rate {pass_rate:.2%} < {cfg.pass_rate:.0%}")
    if cfg.critical_must_pass_all:
        crit = sorted({r["scenario_id"] for r in runs if r.get("risk") == "critical" and r["status"] != "passed"})
        if crit:
            reasons.append(f"critical scenarios not passed: {', '.join(crit)}")
    if errors:
        reasons.append(f"{errors} run(s) with harness errors")
    ok = not reasons
    return {
        "passed": passed, "failed": failed, "errors": errors,
        "pass_rate": None if pass_rate is None else round(pass_rate, 4),
        "gate_passed": ok,
        "gate_detail": "gate passed" if ok else "; ".join(reasons),
    }
