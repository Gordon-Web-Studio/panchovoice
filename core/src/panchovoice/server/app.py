"""FastAPI app of the core server: REST API (contract section 7), mock tools (section 4) and the built panel."""

from __future__ import annotations

import concurrent.futures
import json
import mimetypes
import re
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

import yaml
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from .. import __version__, db, onboarding, panchito
from ..config import Config
from ..errors import FieldError
from ..fixture import validate_fixture_text
from ..llm import LLMProvider
from ..mocktools import DEFAULT_REGISTRY, SessionRegistry, make_mock_router
from ..probe import agent_reachable, find_web_out, llm_ready, stt_importable, tts_ok
from ..runner import iso, utc_now
from ..scenario import KEBAB, check_scenario_refs, list_scenario_files, validate_scenario_text
from .suites import SuiteBusy, SuiteManager

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
AUDIO = {"stereo": "stereo.wav", "caller": "caller.wav", "agent": "agent.wav"}
CORS_ORIGINS = ["http://localhost:3700", "http://127.0.0.1:3700"]
MISSING_PANEL = """<!doctype html><html><head><meta charset="utf-8"><title>PanchoVoiceAI</title></head>
<body style="font-family:system-ui,sans-serif;max-width:40rem;margin:4rem auto;padding:0 1rem;line-height:1.5">
<h1>PanchoVoiceAI server is running</h1>
<p>The admin panel is not built yet. Build it once, then reload this page:</p>
<pre style="background:#f3f3f3;padding:1rem">cd web
npm install
npm run build</pre>
<p>The API works now: <a href="/api/health">/api/health</a></p></body></html>"""

mimetypes.add_type("text/plain", ".txt")
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")


def _safe_id(value: str, what: str = "id") -> str:
    if not ID_RE.match(value or "") or ".." in value:
        raise HTTPException(status_code=400, detail=f"invalid {what}: {value!r}")
    return value


def _errors(errs: list[FieldError]) -> list[dict[str, str]]:
    return [e.to_dict() for e in errs]


def _suite_status(s: str | None) -> str:
    return "finished" if s == "done" else (s or "running")


def _nearest_rank(values: list[float], q: float) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    k = max(1, int(-(-q * len(vals) // 1)))  # ceil(q * n)
    return vals[min(k, len(vals)) - 1]


class _Cache:
    def __init__(self, ttl: float):
        self.ttl = ttl
        self._v: Any = None
        self._t = 0.0
        self._lock = threading.Lock()

    def get(self, fn: Callable[[], Any]) -> Any:
        with self._lock:
            if self._v is not None and time.monotonic() - self._t < self.ttl:
                return self._v
        v = fn()
        with self._lock:
            self._v, self._t = v, time.monotonic()
        return v


def create_app(
    config: Config,
    *,
    registry: SessionRegistry | None = None,
    mock_base_url: str | None = None,
    run_kwargs: dict[str, Any] | None = None,
    llm_factory: Callable[[], LLMProvider] | None = None,
    web_dir: Path | None = None,
    health_ttl: float = 3.0,
) -> FastAPI:
    """Build the app. `run_kwargs` go to run_suite (tests pass a tone TTS and fast timings)."""
    reg = registry or DEFAULT_REGISTRY
    app = FastAPI(title="PanchoVoiceAI", version=__version__)
    app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])
    app.include_router(make_mock_router(reg))
    kwargs = {"registry": reg, **(run_kwargs or {})}
    if mock_base_url:
        kwargs["mock_base_url"] = mock_base_url
    suites = SuiteManager(config, kwargs)
    app.state.config = config
    app.state.suites = suites
    health_cache = _Cache(health_ttl)
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="pv-probe")

    # ------------------------------------------------------------------ health, config, agents

    def _agents_reach() -> dict[str, bool]:
        futs = {a.id: pool.submit(agent_reachable, a.url, 0.5) for a in config.agents}
        return {k: bool(f.result()) for k, f in futs.items()}

    def _health() -> dict[str, Any]:
        f_tts = pool.submit(tts_ok, config.tts.url, 0.6)
        f_llm = pool.submit(llm_ready, config.llm, 0.6)
        agents = _agents_reach()
        llm_ok, llm_detail = f_llm.result()
        checks = {"tts": bool(f_tts.result()), "stt": stt_importable(), "llm": bool(llm_ok), "agents": agents}
        ok = checks["tts"] and checks["stt"] and checks["llm"] and all(agents.values())
        return {"ok": ok, "version": __version__, "checks": checks,
                "details": {"llm": f"{config.llm.provider}: {llm_detail}", "tts_url": config.tts.url}}

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return health_cache.get(_health)

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        return config.public_dict()

    @app.get("/api/agents")
    def agents() -> list[dict[str, Any]]:
        reach = _agents_reach()
        return [{**a.model_dump(mode="json"), "reachable": reach.get(a.id, False)} for a in config.agents]

    # ------------------------------------------------------------------ scenarios

    def _scenario_file(sid: str) -> Path | None:
        for p in list_scenario_files(config.scenarios_path):
            if p.stem == sid:
                return p
        return None

    def _rel(p: Path) -> str:
        try:
            return p.resolve().relative_to(config.root.resolve()).as_posix()
        except ValueError:
            return str(p)

    def _validate_scenario(text: str, filename: str | None) -> list[FieldError]:
        scn, errs = validate_scenario_text(text, filename)
        if scn is not None and not errs:
            errs = check_scenario_refs(scn, config)
        return errs

    @app.get("/api/scenarios")
    def list_scenarios() -> list[dict[str, Any]]:
        out = []
        for p in list_scenario_files(config.scenarios_path):
            text = p.read_text(encoding="utf-8")
            try:
                raw = yaml.safe_load(text)
            except yaml.YAMLError:
                raw = None
            raw = raw if isinstance(raw, dict) else {}
            caller = raw.get("caller") if isinstance(raw.get("caller"), dict) else {}
            errs = _validate_scenario(text, p.name)
            out.append({
                "id": p.stem, "title": str(raw.get("title") or p.stem), "agent": raw.get("agent") or "",
                "risk": raw.get("risk") or "normal", "tags": raw.get("tags") if isinstance(raw.get("tags"), list) else [],
                "language": raw.get("language") or "en", "mode": caller.get("mode") or "scripted", "file": _rel(p),
                "valid": not errs, "errors": _errors(errs),
            })
        return out

    @app.post("/api/scenarios/validate")
    def validate_scenario(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        errs = _validate_scenario(str(body.get("yaml") or ""), None)
        return {"ok": not errs, "errors": _errors(errs)}

    @app.get("/api/scenarios/{sid}")
    def get_scenario(sid: str) -> dict[str, Any]:
        p = _scenario_file(_safe_id(sid))
        if p is None:
            raise HTTPException(status_code=404, detail=f"scenario {sid} not found")
        text = p.read_text(encoding="utf-8")
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError:
            parsed = None
        errs = _validate_scenario(text, p.name)
        return {"id": sid, "yaml": text, "parsed": parsed, "file": _rel(p), "valid": not errs, "errors": _errors(errs)}

    @app.put("/api/scenarios/{sid}")
    def put_scenario(sid: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        if not re.match(KEBAB, sid or ""):
            return {"ok": False, "errors": [{"path": "id", "message": f"id '{sid}' must be kebab-case"}]}
        text = str(body.get("yaml") or "")
        errs = _validate_scenario(text, f"{sid}.yaml")
        if errs:
            return {"ok": False, "errors": _errors(errs)}
        path = _scenario_file(sid) or (config.scenarios_path / f"{sid}.yaml")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8", newline="\n")
        onboarding.mark(config, "scenario_created")
        return {"ok": True, "errors": [], "file": _rel(path)}

    @app.delete("/api/scenarios/{sid}")
    def delete_scenario(sid: str) -> dict[str, Any]:
        p = _scenario_file(_safe_id(sid))
        if p is None:
            raise HTTPException(status_code=404, detail=f"scenario {sid} not found")
        p.unlink()
        return {"ok": True}

    # ------------------------------------------------------------------ fixtures

    def _fixture_file(fid: str) -> Path:
        return config.fixtures_path / f"{_safe_id(fid)}.json"

    @app.get("/api/fixtures")
    def list_fixtures() -> list[dict[str, Any]]:
        out = []
        if config.fixtures_path.is_dir():
            for p in sorted(config.fixtures_path.glob("*.json")):
                fx, errs = validate_fixture_text(p.read_text(encoding="utf-8"), p.name)
                desc = ""
                try:
                    desc = str(json.loads(p.read_text(encoding="utf-8")).get("description") or "")
                except (ValueError, AttributeError):
                    pass
                out.append({"id": p.stem, "description": desc, "file": _rel(p),
                            "tools": [t.name for t in fx.tools] if fx else [], "valid": not errs})
        return out

    @app.get("/api/fixtures/{fid}")
    def get_fixture(fid: str) -> dict[str, Any]:
        p = _fixture_file(fid)
        if not p.is_file():
            raise HTTPException(status_code=404, detail=f"fixture {fid} not found")
        text = p.read_text(encoding="utf-8")
        try:
            data: Any = json.loads(text)
        except ValueError:
            data = text
        return {"id": fid, "json": data}

    @app.put("/api/fixtures/{fid}")
    def put_fixture(fid: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        p = _fixture_file(fid)
        raw = body.get("json")
        if isinstance(raw, str):
            text = raw
        else:
            text = json.dumps(raw, indent=2, ensure_ascii=False)
        _fx, errs = validate_fixture_text(text, p.name)
        if errs:
            return {"ok": False, "errors": _errors(errs)}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8", newline="\n")
        return {"ok": True, "errors": []}

    # ------------------------------------------------------------------ suites

    def _suite_out(s: dict[str, Any], with_runs: bool) -> dict[str, Any]:
        out = {k: v for k, v in s.items() if with_runs or k != "runs"}
        out["status"] = _suite_status(s.get("status"))
        if with_runs:
            out["runs"] = [r for r in (s.get("runs") or []) if r]
        return out

    @app.post("/api/suites")
    def start_suite(body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
        b = body or {}
        ids = b.get("scenario_ids") or None
        tags = b.get("tags") or None
        runs = b.get("runs")
        try:
            sid = suites.start(ids, tags, b.get("agent_id") or None, int(runs) if runs else None)
        except SuiteBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"suite_id": sid}

    @app.get("/api/suites")
    def list_suites(limit: int = 50) -> list[dict[str, Any]]:
        rows = db.list_suites(config.db_path, limit)
        known = {r["suite_id"] for r in rows}
        out = []
        for sid, mem in suites.memory.items():  # active suite before sqlite has it
            if sid not in known:
                out.append(_suite_out(mem, False))
        for r in rows:
            if r.get("status") == "running" and not (r["suite_id"] == suites.active_id and suites.busy()):
                r["status"] = "interrupted"
            out.append(_suite_out(r, False))
        return out

    @app.get("/api/suites/{sid}")
    def get_suite(sid: str) -> dict[str, Any]:
        s = suites.get(_safe_id(sid))
        if s is None:
            raise HTTPException(status_code=404, detail=f"suite {sid} not found")
        return _suite_out(s, True)

    # ------------------------------------------------------------------ runs

    @app.get("/api/runs")
    def list_runs(scenario_id: str | None = None, suite_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        rows = db.list_runs(config.db_path, scenario_id=scenario_id or None, suite_id=suite_id or None,
                            limit=max(1, min(limit, 1000)))
        for r in rows:
            r.pop("path", None)
        return rows

    def _run_dir(rid: str) -> Path:
        d = config.runs_path / _safe_id(rid, "run id")
        if not (d / "result.json").is_file():
            raise HTTPException(status_code=404, detail=f"run {rid} not found")
        return d

    @app.get("/api/runs/{rid}")
    def get_run(rid: str) -> dict[str, Any]:
        d = _run_dir(rid)
        result = json.loads((d / "result.json").read_text(encoding="utf-8"))
        try:
            onboarding.mark(config, "viewed_report")
        except OSError:
            pass
        return result

    @app.get("/api/runs/{rid}/audio/{name}")
    def get_audio(rid: str, name: str) -> FileResponse:
        fname = AUDIO.get(name.removesuffix(".wav"))
        if fname is None:
            raise HTTPException(status_code=404, detail="audio name must be stereo, caller or agent")
        f = _run_dir(rid) / fname
        if not f.is_file():
            raise HTTPException(status_code=404, detail=f"{fname} not found for run {rid}")
        return FileResponse(f, media_type="audio/wav", filename=f"{rid}-{fname}")

    @app.get("/api/runs/{rid}/transcript")
    def get_transcript(rid: str) -> dict[str, Any]:
        f = _run_dir(rid) / "transcript.json"
        if not f.is_file():
            raise HTTPException(status_code=404, detail="no transcript for this run")
        return json.loads(f.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------ stats, onboarding, panchito

    @app.get("/api/stats")
    def stats() -> dict[str, Any]:
        if not config.db_path.exists():
            return {"suites": 0, "runs": 0, "pass_rate_7d": None, "latency_p50_7d": None,
                    "latency_p95_7d": None, "last_suite": None}
        con = db.connect(config.db_path)
        try:
            n_suites = con.execute("SELECT COUNT(*) FROM suites").fetchone()[0]
            n_runs = con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        finally:
            con.close()
        since = iso(utc_now() - timedelta(days=7))
        recent = db.list_runs(config.db_path, since=since, limit=100000)
        passed = sum(1 for r in recent if r["status"] == "passed")
        failed = sum(1 for r in recent if r["status"] == "failed")
        p50 = _nearest_rank([r["latency_p50_ms"] for r in recent], 0.5)
        p95 = _nearest_rank([r["latency_p95_ms"] for r in recent], 0.95)
        last = next((s for s in list_suites(10) if s["status"] != "running"), None)
        return {"suites": n_suites, "runs": n_runs,
                "pass_rate_7d": round(passed / (passed + failed), 4) if passed + failed else None,
                "latency_p50_7d": p50, "latency_p95_7d": p95, "last_suite": last}

    @app.get("/api/onboarding")
    def get_onboarding() -> dict[str, Any]:
        return {"steps": onboarding.steps(config)}

    @app.post("/api/onboarding/{step}")
    def post_onboarding(step: str) -> dict[str, Any]:
        try:
            onboarding.mark(config, step)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown step {step}; known: {', '.join(onboarding.STEP_IDS)}")
        return {"ok": True}

    @app.post("/api/panchito/chat")
    def panchito_chat(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        messages = body.get("messages")
        if not isinstance(messages, list):
            raise HTTPException(status_code=400, detail="messages must be a list of {role, content}")
        ctx = body.get("context") if isinstance(body.get("context"), dict) else None
        return panchito.chat(config, messages, ctx, provider_factory=llm_factory)

    # ------------------------------------------------------------------ the built panel

    panel = web_dir if web_dir is not None else find_web_out(config)
    app.state.web_dir = panel

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def static(full_path: str, request: Request) -> Response:
        if full_path.startswith(("api/", "mock/")) or full_path in ("api", "mock"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if panel is None or not (panel / "index.html").is_file():
            if full_path in ("", "index.html"):
                return HTMLResponse(MISSING_PANEL)
            return HTMLResponse(MISSING_PANEL, status_code=404)
        root = panel.resolve()
        target = (root / full_path).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        cands = [target, target / "index.html"]
        if target != root:
            cands.append(target.with_name(target.name + ".html"))
        for cand in cands:
            if cand.is_file():
                return FileResponse(cand)
        nf = root / "404.html"
        if nf.is_file():
            return FileResponse(nf, status_code=404, media_type="text/html")
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    return app
