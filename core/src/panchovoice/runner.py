"""Run one scenario (a call) or a suite, write the run files and index them in sqlite."""

from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import db
from .adapters.http_turn import HttpCallRecording, HttpTurnCall
from .adapters.websocket import AdapterError, CallRecording, WsCall
from .audio.noise import add_noise
from .audio.pcm import samples_to_ms, write_wav
from .audio.tts import TTS, KokoroTTS
from .caller import AICaller, make_caller
from .checks import (check_barge_in, check_final_state, check_judge, check_latency, check_phrases, check_tools,
                     check_wer, gate, run_status)
from .config import Config
from .fixture import Fixture, load_fixture, resolve_fixture
from .judge import render_transcript, run_judge
from .llm import LLMProvider, make_provider
from .metrics import TurnTiming, latency_summary, measure_turns, wer
from .mocktools import DEFAULT_REGISTRY, MockServer, SessionRegistry, start_mock_server
from .scenario import Scenario, load_all_scenarios

ProgressFn = Callable[[dict[str, Any]], None]

# --------------------------------------------------------------------------- ids and time


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id(now: datetime | None = None) -> str:
    return f"r_{(now or utc_now()).strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}"


def new_suite_id(config: Config, now: datetime | None = None) -> str:
    base = f"s_{(now or utc_now()).strftime('%Y%m%d_%H%M%S')}"
    sid, n = base, 2
    while db.suite_exists(config.db_path, sid) or (config.data_path / "suites" / f"{sid}.json").exists():
        sid, n = f"{base}_{n}", n + 1
    return sid


# --------------------------------------------------------------------------- shared mock server

_mock_lock = threading.Lock()
_mock_server: MockServer | None = None


def ensure_mock_server(port: int = 8700, registry: SessionRegistry | None = None) -> str:
    """Start the standalone mock server once per process; return its base url (…/mock)."""
    global _mock_server
    with _mock_lock:
        if _mock_server is None:
            _mock_server = start_mock_server(port, registry or DEFAULT_REGISTRY)
        return _mock_server.base_url


# --------------------------------------------------------------------------- TTS with noise and memory cache


class CallerVoice:
    def __init__(self, tts: TTS, voice: str, speed: float, noise: str, snr_db: float):
        self.tts, self.voice, self.speed, self.noise, self.snr_db = tts, voice, speed, noise, snr_db
        self._cache: dict[str, np.ndarray] = {}
        self._n = 0

    def __call__(self, text: str) -> np.ndarray:
        if text not in self._cache:
            audio = self.tts.synth(text, self.voice, self.speed)
            self._n += 1
            if self.noise != "none":
                audio = add_noise(audio, self.noise, self.snr_db, seed=7 + self._n)
            self._cache[text] = audio
        return self._cache[text]


# --------------------------------------------------------------------------- run one scenario


def _transcripts(rec: CallRecording, role: str, lo_ms: int, hi_ms: int | None) -> str:
    parts = []
    for m in rec.messages:
        if m.type != "transcript" or m.data.get("role") != role:
            continue
        if role == "user" and m.data.get("final") is False:
            continue
        if m.t_ms >= lo_ms and (hi_ms is None or m.t_ms < hi_ms):
            t = str(m.data.get("text") or "").strip()
            if t:
                parts.append(t)
    return " ".join(parts)


def _stt_window(rec: CallRecording, start_ms: int | None, end_ms: int | None, language: str, model: str) -> str:
    if start_ms is None or end_ms is None or end_ms <= start_ms:
        return ""
    from .audio.stt import transcribe

    a = rec.agent_track[start_ms * 16: end_ms * 16]
    return transcribe(a, language, model)


def build_turns(rec: CallRecording, vad: str, stt_fallback: bool, language: str, stt_model: str) -> tuple[list[dict], list[dict]]:
    """Per turn rows of contract section 6, plus transcript lines [{t_ms, role, text, source}]."""
    timings = []
    for i, t in enumerate(rec.turns):
        nxt = rec.turns[i + 1].caller_start_ms if i + 1 < len(rec.turns) else None
        timings.append(TurnTiming(t.caller_start_ms, t.caller_end_ms, nxt, interrupted=t.interrupt_after_ms is not None))
    measures = measure_turns(rec.agent_track, timings, vad=vad)
    rows: list[dict] = []
    lines: list[dict] = []
    first_start = rec.turns[0].caller_start_ms if rec.turns else None
    greet = _transcripts(rec, "agent", 0, first_start)
    if not greet and rec.greeting and stt_fallback and first_start:
        greet = _stt_window(rec, 0, first_start, language, stt_model)
    if greet:
        lines.append({"t_ms": 0, "role": "agent", "text": greet, "source": "agent"})
    for i, (t, tm, m) in enumerate(zip(rec.turns, timings, measures)):
        heard = _transcripts(rec, "user", t.caller_start_ms, tm.next_caller_start_ms)
        agent_text = _transcripts(rec, "agent", t.caller_end_ms, tm.next_caller_start_ms)
        source = "agent"
        if not agent_text and stt_fallback and m.agent_start_ms is not None:
            try:
                agent_text = _stt_window(rec, m.agent_start_ms, m.agent_end_ms, language, stt_model)
                source = "stt"
            except Exception:
                agent_text = ""
        interrupted = t.interrupt_after_ms is not None and m.stop_ms is not None
        rows.append({
            "index": i,
            "caller_text": t.caller_text,
            "caller_start_ms": t.caller_start_ms,
            "caller_end_ms": t.caller_end_ms,
            "agent_heard": heard,
            "agent_text": agent_text,
            "agent_start_ms": m.agent_start_ms,
            "agent_end_ms": m.agent_end_ms,
            "latency_ms": m.latency_ms,
            "interrupted": interrupted,
            "stop_ms": m.stop_ms if t.interrupt_after_ms is not None else None,
            "_planned_interrupt": t.interrupt_after_ms is not None,
        })
        lines.append({"t_ms": t.caller_start_ms, "role": "caller", "text": t.caller_text, "source": "script"})
        if agent_text:
            lines.append({"t_ms": m.agent_start_ms if m.agent_start_ms is not None else t.caller_end_ms,
                          "role": "agent", "text": agent_text, "source": source})
    return rows, lines


def _error_result(base: dict[str, Any], message: str) -> dict[str, Any]:
    base.update({"status": "error", "error": message})
    return base


def run_scenario(
    scenario: Scenario,
    config: Config,
    *,
    suite_id: str | None = None,
    llm: LLMProvider | None = None,
    tts: TTS | None = None,
    registry: SessionRegistry | None = None,
    mock_base_url: str | None = None,
    vad: str = "silero",
    stt_fallback: bool = True,
    on_event: ProgressFn | None = None,
    write_index: bool = True,
    timing: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Run one call of `scenario` against its agent and write data/runs/<run_id>/.

    `timing` overrides adapter timings (end_of_turn_ms, first_turn_wait_ms, ...), mainly for tests.
    Returns the run result dict (contract section 6).
    """
    started = utc_now()
    run_id = new_run_id(started)
    run_dir = config.runs_path / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    emit = on_event or (lambda e: None)
    reg = registry or DEFAULT_REGISTRY
    provider = llm
    result: dict[str, Any] = {
        "run_id": run_id,
        "suite_id": suite_id,
        "scenario_id": scenario.id,
        "agent_id": scenario.agent,
        "status": "error",
        "started_at": iso(started),
        "duration_ms": 0,
        "turns": [],
        "tool_calls": [],
        "final_state": {},
        "metrics": {"latency_p50_ms": None, "latency_p95_ms": None, "latency_max_ms": None, "wer": None,
                    "barge_in_stop_ms": None, "turn_count": 0},
        "checks": [],
        "cost": {"llm_calls": 0, "usd_estimate": 0.0},
        "artifacts": {},
        "error": None,
        "risk": scenario.risk,
        "title": scenario.title,
        "language": scenario.language,
        "mode": scenario.caller.mode,
        "notes": [],
    }
    session = None
    try:
        agent = config.agent(scenario.agent)
        if agent is None:
            return _error_result(result, f"agent '{scenario.agent}' is not in panchovoice.yaml")
        if agent.adapter not in ("websocket", "http-turn"):
            return _error_result(result, f"adapter '{agent.adapter}' is not available yet")
        fixture: Fixture | None = None
        if scenario.fixture:
            fixture = load_fixture(resolve_fixture(scenario.fixture, config.fixtures_path))
        else:
            fixture = Fixture(id="empty", description="no fixture", tools=[], state={})
        session = reg.create(fixture)
        base = mock_base_url or ensure_mock_server(registry=reg)
        mock_url = f"{base.rstrip('/')}/{session.session_id}"

        needs_llm = scenario.caller.mode == "ai" or bool(scenario.expect.judge)
        if provider is None and needs_llm:
            provider = make_provider(config.llm)
        caller = make_caller(scenario, provider)
        voice = CallerVoice(tts or KokoroTTS(config.tts.url, config.data_path / "cache" / "tts"),
                            scenario.caller.voice, scenario.caller.speed, scenario.audio.noise, scenario.audio.snr_db)
        # synthesize known lines before connecting, so TTS time never shows up as caller delay
        for text in caller.upcoming_texts():
            voice(text)

        stt_model = config.stt.model

        def fallback_text(seg: np.ndarray) -> str:
            from .audio.stt import transcribe
            return transcribe(seg, scenario.language, stt_model)

        common = dict(
            session_id=session.session_id,
            language=scenario.language,
            mock_tools_url=mock_url,
            max_seconds=scenario.limits.max_seconds,
            silence_timeout_s=scenario.limits.silence_timeout_s,
            on_t0=session.start_clock,
            on_event=lambda e: emit({**e, "run_id": run_id, "scenario_id": scenario.id}),
            **(timing or {}),
        )
        emit({"type": "call_started", "run_id": run_id, "scenario_id": scenario.id})
        try:
            if agent.adapter == "http-turn":
                assert agent.http is not None
                rec = HttpTurnCall(agent.http, caller, voice, **common).run()
            else:
                ws_call = WsCall(
                    agent.url, caller, voice,
                    agent_text_fallback=fallback_text if (stt_fallback and isinstance(caller, AICaller)) else None,
                    **common,
                )
                rec = asyncio.run(ws_call.run())
        except AdapterError as exc:
            return _error_result(result, str(exc))
        result["duration_ms"] = rec.duration_ms
        result["notes"] = list(rec.notes)
        if isinstance(caller, AICaller) and caller.errors:
            result["notes"] += [f"ai caller: {e}" for e in caller.errors]
        if not rec.turns:
            msg = "the caller never spoke"
            if isinstance(caller, AICaller) and caller.errors:
                msg += f" (ai caller error: {caller.errors[0]})"
            _write_audio(run_dir, rec, result)
            return _error_result(result, msg)

        # recording files
        _write_audio(run_dir, rec, result)

        # metrics
        rows, lines = build_turns(rec, vad, stt_fallback, scenario.language, stt_model)
        http_rec = rec if isinstance(rec, HttpCallRecording) else None
        if http_rec is not None:
            _apply_http_stats(rows, lines, http_rec)
        lat = latency_summary(r["latency_ms"] for r in rows)
        w = None
        if any(r["agent_heard"] for r in rows):
            w = wer([r["caller_text"] for r in rows], [r["agent_heard"] for r in rows])
        stops = [r["stop_ms"] for r in rows if r["stop_ms"] is not None]
        metrics = {**lat, "wer": w, "barge_in_stop_ms": max(stops) if stops else None, "turn_count": len(rows)}

        calls = session.get_calls()
        if http_rec is not None:
            # the mock server logs wall clock ms; move them onto the built timeline, then add mapped actions
            for c in calls:
                c["t_ms"] = http_rec.timeline_ms(c["t_ms"])
                c["source"] = "mock"
            calls = sorted(calls + [dict(c) for c in http_rec.action_calls], key=lambda c: c["t_ms"])
        state = session.get_state()
        agent_text = " ".join(l["text"] for l in lines if l["role"] == "agent")

        checks: list[dict[str, Any]] = []
        replied = sum(1 for r in rows if r["agent_start_ms"] is not None)
        checks.append({"id": "agent_replied", "kind": "call", "passed": replied > 0,
                       "status": "passed" if replied > 0 else "failed",
                       "detail": f"{replied} of {len(rows)} caller turns got a spoken reply"})
        if rec.ended_by == "agent_closed":
            checks.append({"id": "call_completed", "kind": "call", "passed": False, "status": "failed",
                           "detail": "the agent closed the connection without an end message"})
        e = scenario.expect
        checks += check_tools(e, calls)
        checks += check_final_state(e, state)
        checks += check_latency(e, metrics)
        checks += check_barge_in(e, rows, scenario.has_interrupts)
        checks += check_wer(e, metrics)
        checks += check_phrases(e, agent_text)
        if e.judge:
            transcript_text = render_transcript(lines, calls)
            assert provider is not None
            checks += check_judge(run_judge(provider, e.judge, transcript_text, scenario.language))
        if http_rec is not None:
            checks = _http_turn_checks(checks, e, http_rec)

        for r in rows:
            r.pop("_planned_interrupt", None)
        result.update({
            "status": run_status(checks),
            "turns": rows,
            "tool_calls": calls,
            "final_state": state,
            "metrics": metrics,
            "checks": checks,
            "ended_by": rec.ended_by,
        })
        _write_transcript(run_dir, lines, rec)
        result["artifacts"]["transcript"] = "transcript.json"
        return result
    except Exception as exc:  # harness failure: never an agent failure
        result["notes"].append(traceback.format_exc(limit=5))
        return _error_result(result, f"{type(exc).__name__}: {exc}")
    finally:
        if provider is not None:
            result["cost"] = {"llm_calls": provider.usage.calls, "usd_estimate": round(provider.usage.usd_estimate, 6),
                              "provider": provider.name}
        if session is not None:
            if not result["tool_calls"]:
                result["tool_calls"] = session.get_calls()
            if not result["final_state"]:
                result["final_state"] = session.get_state()
            reg.remove(session.session_id)
        (run_dir / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        if write_index:
            try:
                db.insert_run(config.db_path, result, run_dir, scenario.risk)
            except Exception as exc:
                result["notes"].append(f"db index failed: {exc}")
        emit({"type": "run_finished", "run_id": run_id, "scenario_id": scenario.id, "status": result["status"]})


def _apply_http_stats(rows: list[dict], lines: list[dict], rec: HttpCallRecording) -> None:
    """Add the per stage times of http-turn, and fill the reply time when there is no agent audio (text mode)."""
    for row, st in zip(rows, rec.stats):
        row.update({"stt_ms": st.stt_ms, "llm_ms": st.llm_ms, "tts_ms": st.tts_ms, "pipeline_ms": st.pipeline_ms})
        if row["agent_start_ms"] is None and st.agent_start_ms is not None:
            row["agent_start_ms"] = st.agent_start_ms
            row["agent_end_ms"] = st.agent_end_ms
            row["latency_ms"] = st.agent_start_ms - row["caller_end_ms"]
            for line in lines:
                if line["role"] == "agent" and line["t_ms"] == row["caller_end_ms"] and line["text"] == row["agent_text"]:
                    line["t_ms"] = st.agent_start_ms


def _http_turn_checks(checks: list[dict[str, Any]], expect: Any, rec: HttpCallRecording) -> list[dict[str, Any]]:
    """Checks that http-turn cannot measure become `error` with a clear reason."""
    out = [c for c in checks if c["kind"] != "barge_in"]
    if expect.barge_in is not None:
        out.append({"id": "barge_in", "kind": "barge_in", "passed": None, "status": "error",
                    "detail": "not supported by http-turn"})
    if rec.text_mode:
        for c in out:
            if c["kind"] == "wer":
                c.update({"passed": None, "status": "error",
                          "detail": "not measured: the http-turn agent has no transcribe step (text mode)"})
    return out


def _write_audio(run_dir: Path, rec: CallRecording, result: dict[str, Any]) -> None:
    write_wav(run_dir / "caller.wav", rec.caller_track)
    write_wav(run_dir / "agent.wav", rec.agent_track)
    write_wav(run_dir / "stereo.wav", np.stack([rec.caller_track, rec.agent_track], axis=1))
    result["artifacts"].update({"stereo_wav": "stereo.wav", "caller_wav": "caller.wav", "agent_wav": "agent.wav"})


def _write_transcript(run_dir: Path, lines: list[dict], rec: CallRecording) -> None:
    data = {
        "lines": sorted(lines, key=lambda l: l["t_ms"]),
        "events": [{"t_ms": m.t_ms, **m.data} for m in rec.messages],
        "ended_by": rec.ended_by,
        "duration_ms": samples_to_ms(len(rec.caller_track)),
    }
    (run_dir / "transcript.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------- suites


def select_scenarios(config: Config, scenario_ids: list[str] | None = None, tags: list[str] | None = None,
                     agent_id: str | None = None) -> tuple[list[Scenario], dict[str, list]]:
    good, bad = load_all_scenarios(config)
    by_id = {s.id: s for s in good}
    if scenario_ids:
        missing = [i for i in scenario_ids if i not in by_id]
        if missing:
            bad_names = {Path(n).stem for n in bad}
            why = [f"{i} (invalid file)" if i in bad_names else f"{i} (not found)" for i in missing]
            raise ValueError("unknown or invalid scenarios: " + ", ".join(why))
        chosen = [by_id[i] for i in scenario_ids]
    else:
        chosen = good
    if tags:
        chosen = [s for s in chosen if set(tags) & set(s.tags)]
    if agent_id:
        chosen = [s for s in chosen if s.agent == agent_id]
    return chosen, bad


def run_summary(result: dict[str, Any]) -> dict[str, Any]:
    m = result.get("metrics") or {}
    return {
        "run_id": result["run_id"], "suite_id": result.get("suite_id"), "scenario_id": result["scenario_id"],
        "status": result["status"], "started_at": result["started_at"], "risk": result.get("risk"),
        "latency_p50_ms": m.get("latency_p50_ms"), "latency_p95_ms": m.get("latency_p95_ms"), "wer": m.get("wer"),
        "duration_ms": result.get("duration_ms"), "error": result.get("error"),
        "failed_checks": [c["id"] for c in result.get("checks", []) if c.get("status") == "failed"],
    }


def run_suite(
    config: Config,
    *,
    scenario_ids: list[str] | None = None,
    tags: list[str] | None = None,
    agent_id: str | None = None,
    runs: int | None = None,
    suite_id: str | None = None,
    on_progress: ProgressFn | None = None,
    llm_factory: Callable[[], LLMProvider] | None = None,
    tts: TTS | None = None,
    registry: SessionRegistry | None = None,
    mock_base_url: str | None = None,
    vad: str = "silero",
    stt_fallback: bool = True,
    timing: dict[str, int] | None = None,
    scenarios: list[Scenario] | None = None,
) -> dict[str, Any]:
    """Run a suite in this thread. `on_progress` gets events; the summary is also kept in sqlite.

    Returns the suite summary with `runs` (run summaries) and `results` (full run results).
    """
    emit = on_progress or (lambda e: None)
    if scenarios is None:
        chosen, invalid = select_scenarios(config, scenario_ids, tags, agent_id)
    else:
        chosen, invalid = scenarios, {}
    started = utc_now()
    sid = suite_id or new_suite_id(config, started)
    plan = [(s, k) for s in chosen for k in range(runs or s.runs)]
    summary: dict[str, Any] = {
        "suite_id": sid, "started_at": iso(started), "finished_at": None, "status": "running",
        "agent_id": agent_id, "total": len(plan), "done": 0, "passed": 0, "failed": 0, "errors": 0,
        "pass_rate": None, "gate_passed": None, "gate_detail": "",
        "scenario_ids": [s.id for s in chosen], "skipped_invalid": sorted(invalid), "runs": [],
    }
    suites_dir = config.data_path / "suites"
    suites_dir.mkdir(parents=True, exist_ok=True)

    def save() -> None:
        db.upsert_suite(config.db_path, summary)
        (suites_dir / f"{sid}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    save()
    emit({"type": "suite_started", "suite_id": sid, "total": len(plan)})
    results: list[dict[str, Any]] = []
    for i, (scn, k) in enumerate(plan):
        emit({"type": "run_started", "suite_id": sid, "scenario_id": scn.id, "index": i, "total": len(plan), "repeat": k})
        llm = llm_factory() if llm_factory else None
        res = run_scenario(scn, config, suite_id=sid, llm=llm, tts=tts, registry=registry, mock_base_url=mock_base_url,
                           vad=vad, stt_fallback=stt_fallback, on_event=emit, timing=timing)
        results.append(res)
        summary["runs"].append(run_summary(res))
        summary["done"] = len(results)
        g = gate(summary["runs"], config.gate)
        summary.update({k2: g[k2] for k2 in ("passed", "failed", "errors", "pass_rate")})
        save()
        emit({"type": "suite_progress", "suite_id": sid, "done": len(results), "total": len(plan),
              "last": summary["runs"][-1]})
        time.sleep(0.2)  # let the agent reset between calls
    g = gate(summary["runs"], config.gate)
    summary.update(g)
    summary["finished_at"] = iso(utc_now())
    summary["status"] = "done"
    save()
    emit({"type": "suite_finished", "suite_id": sid, "summary": {k: v for k, v in summary.items() if k != "runs"}})
    return {**summary, "results": results}
