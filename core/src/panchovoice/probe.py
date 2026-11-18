"""Small, fast reachability probes shared by the API server, onboarding, doctor and the wizard."""

from __future__ import annotations

import importlib.util
import os
import shutil
import socket
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import AgentConfig, Config, LLMConfig


def local_host(host: str | None) -> str:
    """`localhost` -> `127.0.0.1` (on Windows a client that tries ::1 first can wait about 2 s)."""
    if not host or host == "localhost":
        return "127.0.0.1"
    return host


def fix_local_url(url: str) -> str:
    p = urlparse(url)
    if p.hostname == "localhost":
        netloc = "127.0.0.1" + (f":{p.port}" if p.port else "")
        return p._replace(netloc=netloc).geturl()
    return url


def http_get(url: str, timeout: float = 0.8) -> httpx.Response | None:
    try:
        return httpx.get(fix_local_url(url), timeout=timeout)
    except (httpx.HTTPError, OSError):
        return None


def tcp_ok(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((local_host(host), port), timeout=timeout):
            return True
    except OSError:
        return False


def _host_port(url: str) -> tuple[str, int, str]:
    p = urlparse(url)
    secure = p.scheme in ("wss", "https")
    port = p.port or (443 if secure else 80)
    return local_host(p.hostname), port, ("https" if secure else "http")


def agent_reachable(url: str, timeout: float = 0.6) -> bool:
    """WebSocket agent: HTTP GET http(s)://host:port/health (the demo answers `ok`), else a TCP connect."""
    try:
        host, port, scheme = _host_port(url)
    except ValueError:
        return False
    r = http_get(f"{scheme}://{host}:{port}/health", timeout)
    if r is not None:
        return True  # something answers HTTP on that port
    return tcp_ok(host, port, timeout)


def agent_health_text(url: str, timeout: float = 0.6) -> str | None:
    """Body of GET /health on the agent host, or None."""
    try:
        host, port, scheme = _host_port(url)
    except ValueError:
        return None
    r = http_get(f"{scheme}://{host}:{port}/health", timeout)
    return r.text.strip() if r is not None and r.status_code == 200 else None


def tts_ok(url: str, timeout: float = 0.8) -> bool:
    r = http_get(f"{url.rstrip('/')}/v1/models", timeout)
    return r is not None and r.status_code == 200


def stt_importable() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def ollama_models(url: str = "http://localhost:11434", timeout: float = 1.0) -> list[str] | None:
    """Model names from Ollama /api/tags, or None when Ollama is not reachable."""
    r = http_get(f"{url.rstrip('/')}/api/tags", timeout)
    if r is None or r.status_code != 200:
        return None
    try:
        return [m.get("name", "") for m in r.json().get("models", [])]
    except ValueError:
        return None


def llm_ready(cfg: LLMConfig, timeout: float = 0.8) -> tuple[bool, str]:
    """Is the configured brain usable? Never calls the model."""
    if cfg.provider == "claude-cli":
        exe = shutil.which("claude")
        return (exe is not None, exe or "`claude` is not on PATH")
    if cfg.provider == "anthropic":
        ok = bool(os.environ.get("ANTHROPIC_API_KEY"))
        return ok, "ANTHROPIC_API_KEY is set" if ok else "ANTHROPIC_API_KEY is not set"
    if cfg.provider == "ollama":
        models = ollama_models(cfg.url or "http://localhost:11434", timeout)
        if models is None:
            return False, "Ollama is not reachable"
        if cfg.model not in models and f"{cfg.model}:latest" not in models:
            return False, f"model {cfg.model} is not pulled (ollama pull {cfg.model})"
        return True, f"model {cfg.model} ready"
    return False, f"unknown provider {cfg.provider}"


def repo_root() -> Path:
    """The source checkout root (core/src/panchovoice/probe.py -> repo)."""
    return Path(__file__).resolve().parents[3]


def find_web_out(config: Config | None = None) -> Path | None:
    """The built panel folder (web/out with index.html), next to the config or in the source checkout."""
    cands = []
    if config is not None:
        cands.append(config.root / "web" / "out")
    cands.append(repo_root() / "web" / "out")
    for c in cands:
        if (c / "index.html").is_file():
            return c
    return None


def demo_agent(config: Config) -> AgentConfig | None:
    return config.agent("acme-demo")
