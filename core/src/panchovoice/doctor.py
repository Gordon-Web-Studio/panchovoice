"""`pv doctor`: check prerequisites, print PASS / WARN / FAIL with a fix hint for each."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import Config, load_config
from .errors import ConfigError
from .mocktools import port_is_free
from .probe import (agent_health_text, find_web_out, http_get, llm_ready, ollama_models, stt_importable, stt_ready,
                    tts_ok, tts_ready)

DEMO_MODEL = "qwen2.5:7b"


@dataclass
class Check:
    id: str
    name: str
    status: str  # PASS | WARN | FAIL
    detail: str
    fix: str = ""
    required: bool = False


def _p(id, name, detail, required=False):
    return Check(id, name, "PASS", detail, "", required)


def _bad(id, name, detail, fix, required):
    return Check(id, name, "FAIL" if required else "WARN", detail, fix, required)


def check_python() -> Check:
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 12):
        return _p("python", "Python", ver, True)
    return _bad("python", "Python", f"{ver} (needs 3.12)", "Install Python 3.12, or let uv do it: `uv python install 3.12`", True)


def check_uv() -> Check:
    exe = shutil.which("uv")
    if exe:
        return _p("uv", "uv", exe)
    return _bad("uv", "uv", "not on PATH", "Install uv: https://docs.astral.sh/uv/ (needed by `pv demo`)", False)


def check_docker() -> Check:
    exe = shutil.which("docker")
    if not exe:
        return _bad("docker", "Docker", "not installed",
                    "Optional. Install Docker Desktop to run Kokoro with `docker compose up kokoro`", False)
    try:
        r = subprocess.run([exe, "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return _bad("docker", "Docker", "the daemon did not answer", "Start Docker Desktop", False)
    if r.returncode == 0 and r.stdout.strip():
        return _p("docker", "Docker", f"daemon up (server {r.stdout.strip()})")
    return _bad("docker", "Docker", "installed, daemon is not running", "Start Docker Desktop", False)


def check_tts(cfg: Config) -> Check:
    if cfg.tts.is_cloud:
        name = {"elevenlabs": "ElevenLabs TTS", "deepgram": "Deepgram TTS"}[cfg.tts.provider]
        ok, detail = tts_ready(cfg.tts)
        if ok:
            return _p("tts", name, f"{cfg.tts.key_env} is set, voice {cfg.tts.default_voice}", True)
        return _bad("tts", name, f"{cfg.tts.key_env} is not set",
                    f"Set {cfg.tts.key_env} in your shell (never in panchovoice.yaml)", True)
    if tts_ok(cfg.tts.url, 2.0):
        return _p("tts", "Kokoro TTS", cfg.tts.url, True)
    return _bad("tts", "Kokoro TTS", f"no answer at {cfg.tts.url}/v1/models",
                "Start it: `docker compose -f demo/docker-compose.yml up -d kokoro` (or set tts.url)", True)


def check_ollama(cfg: Config) -> Check:
    url = cfg.llm.url or "http://localhost:11434"
    needed = cfg.llm.provider == "ollama"
    models = ollama_models(url, 2.0)
    if models is None:
        return _bad("ollama", "Ollama", f"not reachable at {url}",
                    "Install from https://ollama.com and start it (the demo agent needs it)", needed)
    want = cfg.llm.model if needed else DEMO_MODEL
    if want in models or f"{want}:latest" in models:
        return _p("ollama", "Ollama", f"{url}, model {want} ready", needed)
    return _bad("ollama", "Ollama", f"running, model {want} is not pulled", f"`ollama pull {want}`", needed)


def check_llm(cfg: Config) -> Check:
    p = cfg.llm.provider
    if p == "ollama":
        ok, detail = llm_ready(cfg.llm, 2.0)
        return _p("llm", "LLM brain", f"ollama: {detail}", True) if ok else _bad(
            "llm", "LLM brain", f"ollama: {detail}", "Start Ollama and pull the model in llm.model", True)
    ok, detail = llm_ready(cfg.llm)
    if ok:
        return _p("llm", "LLM brain", f"{p}: {detail}", True)
    fix = ("Install Claude Code (https://claude.com/claude-code) and log in, or pick another llm.provider"
           if p == "claude-cli" else "Set ANTHROPIC_API_KEY in your shell (never in panchovoice.yaml)")
    return _bad("llm", "LLM brain", f"{p}: {detail}", fix, True)


def _hf_cache() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    home = Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface")
    return home / "hub"


def whisper_cached(model: str) -> bool:
    d = _hf_cache() / f"models--Systran--faster-whisper-{model}" / "snapshots"
    return d.is_dir() and any(d.iterdir())


def check_stt(cfg: Config) -> Check:
    if cfg.stt.is_cloud:
        ok, _detail = stt_ready(cfg.stt)
        if ok:
            return _p("stt", "Deepgram STT", f"{cfg.stt.key_env} is set, model {cfg.stt.model}", True)
        return _bad("stt", "Deepgram STT", f"{cfg.stt.key_env} is not set",
                    f"Set {cfg.stt.key_env} in your shell (never in panchovoice.yaml)", True)
    if not stt_importable():
        return _bad("stt", "faster-whisper", "not importable", "Reinstall: `uv sync` in core/", True)
    m = cfg.stt.model
    if whisper_cached(m):
        return _p("stt", "faster-whisper", f"importable, model {m} cached", True)
    return Check("stt", "faster-whisper", "WARN", f"importable, model {m} not cached yet",
                 "It downloads on the first run (base about 150 MB, small about 480 MB)", True)


def check_elevenlabs_agents(cfg: Config) -> list[Check]:
    out = []
    for a in cfg.agents:
        if a.adapter != "elevenlabs" or a.elevenlabs is None:
            continue
        env = a.elevenlabs.api_key_env
        name = f"ElevenLabs agent {a.id}"
        if os.environ.get(env):
            out.append(_p(f"agent:{a.id}", name, f"{a.elevenlabs.agent_id}, {env} is set", False))
        else:
            out.append(Check(f"agent:{a.id}", name, "WARN", f"{env} is not set",
                             f"Set {env} in your shell: private agents and the tool call record need it", False))
    return out


def check_demo_agent(cfg: Config) -> Check:
    a = cfg.agent("acme-demo")
    if a is None:
        return Check("demo_agent", "Demo agent", "PASS", "not in panchovoice.yaml (skipped)", "")
    if agent_health_text(a.url, 1.0) == "ok":
        return _p("demo_agent", "Demo agent", f"answers at {a.url}")
    return _bad("demo_agent", "Demo agent", f"not reachable at {a.url}", "Start it in another terminal: `pv demo`", False)


def check_web(cfg: Config) -> Check:
    out = find_web_out(cfg)
    if out:
        return _p("web", "Panel build", str(out))
    return _bad("web", "Panel build", "web/out is missing", "`cd web && npm install && npm run build`", False)


def _ours_8700(port: int) -> bool:
    r = http_get(f"http://127.0.0.1:{port}/api/health", 1.5)
    try:
        return r is not None and r.status_code == 200 and "version" in r.json()
    except ValueError:
        return False


def check_ports() -> list[Check]:
    out = []
    for port, name, ours in ((8700, "API server", _ours_8700),
                             (8710, "demo agent", lambda p: agent_health_text(f"ws://127.0.0.1:{p}/ws", 1.0) == "ok")):
        cid = f"port_{port}"
        if port_is_free(port):
            out.append(_p(cid, f"Port {port}", f"free for the {name}"))
        elif ours(port):
            out.append(_p(cid, f"Port {port}", f"used by the PanchoVoiceAI {name}"))
        else:
            out.append(_bad(cid, f"Port {port}", "used by another program",
                            f"Stop that program, or use another port for the {name} (--port)", False))
    return out


def check_disk(cfg: Config) -> Check:
    p = cfg.data_path
    while not p.exists() and p != p.parent:
        p = p.parent
    free_gb = shutil.disk_usage(p).free / 1e9
    if free_gb >= 2:
        return _p("disk", "Free disk", f"{free_gb:.1f} GB", True)
    if free_gb >= 0.5:
        return Check("disk", "Free disk", "WARN", f"{free_gb:.1f} GB", "Free some space: models and recordings need about 2 GB", True)
    return _bad("disk", "Free disk", f"{free_gb:.2f} GB", "Free at least 2 GB", True)


def run_checks(cfg: Config) -> list[Check]:
    checks = [check_python(), check_uv(), check_docker(), check_tts(cfg), check_ollama(cfg), check_llm(cfg),
              check_stt(cfg), check_demo_agent(cfg), *check_elevenlabs_agents(cfg), check_web(cfg), *check_ports(),
              check_disk(cfg)]
    return checks


def failed_required(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c.required and c.status == "FAIL"]


def print_table(checks: list[Check], console) -> None:
    from rich.table import Table

    t = Table(title="PanchoVoiceAI doctor", show_lines=False)
    t.add_column("Result")
    t.add_column("Check")
    t.add_column("Detail", overflow="fold")
    t.add_column("Fix", overflow="fold")
    color = {"PASS": "green", "WARN": "yellow", "FAIL": "red"}
    for c in checks:
        t.add_row(f"[{color[c.status]}]{c.status}[/]", c.name + (" *" if c.required else ""), c.detail, c.fix)
    console.print(t)
    console.print("* required")


def report(checks: list[Check]) -> dict:
    return {"ok": not failed_required(checks), "checks": [asdict(c) for c in checks],
            "summary": {s: sum(1 for c in checks if c.status == s) for s in ("PASS", "WARN", "FAIL")}}


def main(config_path: str | None = None, as_json: bool = False) -> int:
    from rich.console import Console

    console = Console(soft_wrap=True)
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        if as_json:
            print(json.dumps({"ok": False, "error": str(exc), "checks": []}))
        else:
            console.print(f"[red]Config error:[/] {exc}")
        return 1
    checks = run_checks(cfg)
    if as_json:
        print(json.dumps(report(checks), indent=2))
    else:
        print_table(checks, console)
        bad = failed_required(checks)
        if bad:
            console.print(f"[red]{len(bad)} required check(s) failed.[/] Fix them, then run `pv doctor` again.")
        else:
            console.print("[green]Ready.[/] Next: `pv demo` in one terminal, `pv serve --open` in another.")
    return 1 if failed_required(checks) else 0
