"""Panchito, the helper of PanchoVoiceAI (POST /api/panchito/chat).

Panchito answers in JSON `{reply, proposals}`. Proposals are validated here and never written to disk.
"""

from __future__ import annotations

import concurrent.futures
import json
import time
from typing import Any, Callable

import yaml

from .config import Config
from .errors import FieldError
from .fixture import load_fixture, resolve_fixture, validate_fixture_text
from .llm import LLMError, LLMProvider, extract_json, make_provider
from .scenario import check_scenario_refs, list_scenario_files, validate_scenario_text

TIMEOUT_S = 120.0
TIMEOUT_REPLY = ("Sorry, I took too long to think (over 2 minutes) and stopped. "
                 "Please try again, maybe with a shorter question.")

SCHEMA_GUIDE = """\
SCENARIO FILE (YAML, one per file, unknown keys are errors):
id: kebab-case, equals the file name without .yaml
title: text
agent: agent id from panchovoice.yaml
language: en | es
risk: critical | normal
tags: [list]
fixture: file in fixtures/ (for example acme-clinic.json); needed when expect uses tools or final_state
caller:
  mode: scripted | ai
  voice: Kokoro voice id (default af_heart)
  speed: 1.0
  turns: (scripted only, required) list of {say: "text", interrupt_after_ms: optional barge-in ms after the agent starts, pause_ms: optional silence before speaking}
  persona, goal: (ai only, required) text
  facts: {key: value}   opening_line: text   traits: {interrupts: bool, patience_s: number}
audio: {noise: none|cafe|street|white, snr_db: 20}
expect:
  tool_calls: [{name, args: {subset match}}]   forbidden_tools: [names]   no_extra_tools: bool
  order: [tool names, relative order]
  final_state: {collection: [rows, subset match]} (an empty list means the collection must be empty)
  latency_ms: {p50: ms, p95: ms}   barge_in: {max_stop_ms: ms} (needs a turn with interrupt_after_ms)
  wer_max: 0.15   must_say: [substrings]   never_say: [substrings]
  judge: ["yes/no question for the LLM judge"]
limits: {max_turns: 12, max_seconds: 180, silence_timeout_s: 15}
runs: 1

FIXTURE FILE (JSON, id equals the file name without .json):
{"id": "kebab-id", "description": "...",
 "tools": [{"name": "snake_name", "description": "...", "params": {"p": "string|number|integer|boolean"},
            "action": "find|insert|delete", "collection": "name", "match": ["params to match"],
            "returns": "first_or_error|all", "fields": ["optional output fields"],
            "requires": {"collection": "slots", "match": ["day", "time"]} (insert only, optional)}],
 "state": {"collection": [rows]}}
find and delete need a non empty match; their collection must be in state.

METRICS: latency per turn = agent speech onset minus the end of the caller audio (ms); p50 and p95 by nearest rank.
WER = how well the agent heard the caller (0 is perfect). barge_in stop_ms = how fast the agent stopped talking
when the caller interrupted. A run is passed, failed (any check failed) or error (the harness could not measure,
for example the agent was down; errors are not agent failures and are left out of the pass rate).
The gate passes when the pass rate >= gate.pass_rate, every critical scenario passed and there are no errors.
"""

SYSTEM = """\
You are Panchito, the friendly helper inside PanchoVoiceAI, an open source tool that tests voice AI agents:
a caller talks with real audio to the agent under test, both channels are recorded, then the call is scored.
Give short, clear, kind answers in plain words. Answer in the language the user writes in.
When the user asks for a scenario or a fixture, write it in full as a proposal; the user saves it with a button
(you never save files). Reuse the existing fixture and its tool names when they fit. Use only keys from the schema.
When asked why a run failed, name the failed checks, quote the evidence, and suggest one concrete fix.

{schema}
ANSWER FORMAT: one strict JSON object, no prose outside it, no code fences:
{{"reply": "your answer for the user (plain text, may have short lists)",
  "proposals": [{{"kind": "scenario" or "fixture", "id": "the file id", "content": "the full YAML (scenario) or JSON (fixture) text", "summary": "one line"}}]}}
Use "proposals": [] when there is nothing to propose.

PROJECT CONTEXT:
{context}
"""

JSON_HINT = '{"reply": "...", "proposals": [{"kind": "scenario|fixture", "id": "...", "content": "...", "summary": "..."}]}'


# --------------------------------------------------------------------------- context


def _short(v: Any, n: int = 300) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[: n - 3] + "..."


def run_view(config: Config, run_id: str) -> str | None:
    """A compact text view of one run result (failed checks with evidence, metrics, turns, tool calls)."""
    if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id:
        return None
    f = config.runs_path / run_id / "result.json"
    if not f.is_file():
        return None
    try:
        r = json.loads(f.read_text(encoding="utf-8"))
    except ValueError:
        return None
    lines = [f"RUN {r.get('run_id')} of scenario {r.get('scenario_id')} ({r.get('title', '')}), agent {r.get('agent_id')}",
             f"status: {r.get('status')}" + (f", harness error: {r.get('error')}" if r.get("error") else ""),
             f"metrics: {json.dumps(r.get('metrics') or {})}"]
    checks = r.get("checks") or []
    bad = [c for c in checks if c.get("status", "passed" if c.get("passed") else "failed") != "passed"]
    good = [c["id"] for c in checks if c not in bad]
    lines.append(f"check counts (use these exact numbers): {len(checks)} total, {len(bad)} not passed, {len(good)} passed")
    for c in bad:
        status = c.get("status") or ("failed" if c.get("passed") is False else "error")
        ev = f" | evidence: {_short(c['evidence'], 240)}" if c.get("evidence") else ""
        lines.append(f"check {c.get('id')} ({c.get('kind')}): {status}: {_short(c.get('detail', ''), 240)}{ev}")
    if good:
        lines.append("checks passed: " + ", ".join(good))
    for t in (r.get("turns") or [])[:12]:
        lines.append(f"turn {t.get('index')}: caller said {_short(t.get('caller_text', ''), 160)!r}; "
                     f"agent heard {_short(t.get('agent_heard', ''), 160)!r}; agent said {_short(t.get('agent_text', ''), 240)!r}; "
                     f"latency {t.get('latency_ms')} ms")
    for c in (r.get("tool_calls") or [])[:12]:
        lines.append(f"tool call at {c.get('t_ms')} ms: {c.get('name')}({_short(c.get('args'), 160)}) -> {_short(c.get('response'), 120)}")
    if r.get("final_state"):
        lines.append(f"final state: {_short(r['final_state'], 400)}")
    if r.get("ended_by"):
        lines.append(f"call ended by: {r['ended_by']}")
    return "\n".join(lines)


def fixture_tools(config: Config) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    if config.fixtures_path.is_dir():
        for f in sorted(config.fixtures_path.glob("*.json")):
            try:
                out[f.stem] = [t.name for t in load_fixture(f).tools]
            except (ValueError, OSError):
                out[f.stem] = ["(invalid fixture)"]
    return out


def build_context(config: Config, context: dict[str, Any] | None) -> str:
    ctx = context or {}
    parts = []
    ids = [p.stem for p in list_scenario_files(config.scenarios_path)]
    parts.append("scenario ids: " + (", ".join(ids) or "none"))
    tools = fixture_tools(config)
    parts.append("fixtures and their tools: " + ("; ".join(f"{k}.json: {', '.join(v)}" for k, v in tools.items()) or "none"))
    parts.append("agents: " + ", ".join(f"{a.id} ({a.language}, {a.url})" for a in config.agents))
    if ctx.get("page"):
        parts.append(f"the user is on the panel page: {ctx['page']}")
    sid = ctx.get("scenario_id")
    if sid and isinstance(sid, str) and "/" not in sid and ".." not in sid:
        for p in list_scenario_files(config.scenarios_path):
            if p.stem == sid:
                parts.append(f"SCENARIO {sid} (the one the user looks at):\n{p.read_text(encoding='utf-8')[:4000]}")
                break
    rid = ctx.get("run_id")
    if rid:
        view = run_view(config, str(rid))
        parts.append(view if view else f"run {rid} was not found")
    return "\n".join(parts)


# --------------------------------------------------------------------------- proposals


def _content_text(kind: str, content: Any) -> str:
    if isinstance(content, str):
        return content
    if kind == "fixture":
        return json.dumps(content, indent=2, ensure_ascii=False)
    return yaml.safe_dump(content, sort_keys=False, allow_unicode=True)


def validate_proposal(p: dict[str, Any], config: Config, proposed_fixtures: set[str]) -> list[FieldError]:
    kind, pid, text = p["kind"], p["id"], p["content"]
    if kind == "scenario":
        scn, errors = validate_scenario_text(text, f"{pid}.yaml")
        if scn is not None and not errors:
            errors = check_scenario_refs(scn, config)
            fx_id = resolve_fixture(scn.fixture, config.fixtures_path).stem if scn.fixture else None
            if fx_id in proposed_fixtures:  # the fixture is proposed in the same answer
                errors = [e for e in errors if not (e.path == "fixture" and "not found" in e.message)]
        return errors
    if kind == "fixture":
        _fx, errors = validate_fixture_text(text, f"{pid}.json")
        return errors
    return [FieldError("kind", "kind must be scenario or fixture")]


def normalize(raw: Any) -> tuple[str, list[dict[str, Any]]]:
    """Model output -> (reply, proposals with string content)."""
    if not isinstance(raw, dict):
        return (str(raw), [])
    reply = str(raw.get("reply") or "").strip()
    props = []
    for p in raw.get("proposals") or []:
        if not isinstance(p, dict):
            continue
        kind = str(p.get("kind") or "").strip().lower()
        content = _content_text(kind, p.get("content", ""))
        pid = str(p.get("id") or "").strip()
        if pid.endswith((".yaml", ".yml", ".json")):
            pid = pid.rsplit(".", 1)[0]
        if not pid and kind == "scenario":
            try:
                pid = str((yaml.safe_load(content) or {}).get("id") or "")
            except yaml.YAMLError:
                pid = ""
        props.append({"kind": kind, "id": pid, "content": content, "summary": str(p.get("summary") or "").strip()})
    return reply, props


def _check_all(props: list[dict[str, Any]], config: Config) -> dict[int, list[FieldError]]:
    fixtures = {p["id"] for p in props if p["kind"] == "fixture"}
    bad = {}
    for i, p in enumerate(props):
        errs = validate_proposal(p, config, fixtures)
        if errs:
            bad[i] = errs
    return bad


# --------------------------------------------------------------------------- chat


def _clean_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    out = []
    for m in messages or []:
        role = m.get("role")
        content = str(m.get("content") or "")
        if role in ("user", "assistant") and content.strip():
            out.append({"role": role, "content": content})
    # the API wants the conversation to start with the user
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out[-20:]


def _converse(provider: LLMProvider, system: str, messages: list[dict[str, str]], config: Config) -> dict[str, Any]:
    text = provider.complete(system, messages, JSON_HINT)
    try:
        raw = extract_json(text)
    except ValueError:
        return {"reply": text.strip(), "proposals": []}
    reply, props = normalize(raw)
    bad = _check_all(props, config)
    if bad:
        problems = "\n".join(f"proposal {props[i]['kind']} {props[i]['id']!r}: " + "; ".join(str(e) for e in errs)
                             for i, errs in bad.items())
        retry = [*messages, {"role": "assistant", "content": text},
                 {"role": "user", "content": "Some proposals do not pass validation:\n" + problems +
                  "\nFix them and answer again with the full JSON object (same shape, full file contents)."}]
        try:
            text2 = provider.complete(system, retry, JSON_HINT)
            reply2, props2 = normalize(extract_json(text2))
        except (ValueError, LLMError):
            reply2, props2 = reply, props
        if props2:
            reply, props = reply2 or reply, props2
        bad = _check_all(props, config)
        for i, errs in bad.items():
            props[i]["errors"] = [e.to_dict() for e in errs]
    return {"reply": reply or "Here you go.", "proposals": props}


def chat(config: Config, messages: list[dict[str, Any]], context: dict[str, Any] | None = None,
         provider_factory: Callable[[], LLMProvider] | None = None, timeout_s: float = TIMEOUT_S) -> dict[str, Any]:
    """One Panchito turn. Returns {reply, proposals}. A new provider per request: max_calls_per_run caps it."""
    msgs = _clean_messages(messages)
    if not msgs:
        return {"reply": "Hi, I am Panchito. Ask me about a run, or ask me to write a scenario.", "proposals": []}
    system = SYSTEM.format(schema=SCHEMA_GUIDE, context=build_context(config, context))
    provider = provider_factory() if provider_factory else make_provider(config.llm)
    if hasattr(provider, "timeout"):
        provider.timeout = min(float(getattr(provider, "timeout")), timeout_s)
    started = time.monotonic()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="panchito")
    fut = pool.submit(_converse, provider, system, msgs, config)
    try:
        out = fut.result(timeout=timeout_s)
    except concurrent.futures.TimeoutError:
        return {"reply": TIMEOUT_REPLY, "proposals": [], "error": "timeout"}
    except LLMError as exc:
        return {"reply": f"Sorry, my brain ({provider.name}) did not answer: {exc}", "proposals": [], "error": str(exc)}
    finally:
        pool.shutdown(wait=False)
    out["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    out["llm_calls"] = provider.usage.calls
    return out
