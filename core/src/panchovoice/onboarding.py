"""Onboarding checklist (contract section 7): stored in data_dir/onboarding.json, some steps auto detected."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

from . import db
from .config import Config
from .probe import agent_reachable

STEPS: list[tuple[str, str]] = [
    ("demo_running", "Start the demo agent"),
    ("first_run", "Run your first suite"),
    ("viewed_report", "Open a run report"),
    ("agent_added", "Connect your own agent"),
    ("scenario_created", "Create a scenario"),
    ("ci_enabled", "Turn on the CI gate"),
]
STEP_IDS = [s for s, _ in STEPS]
DEMO_AGENT_ID = "acme-demo"
_CI_RE = re.compile(r"\b(pv|panchovoice)\s+run\b")
_lock = threading.Lock()


def _file(config: Config) -> Path:
    return config.data_path / "onboarding.json"


def load(config: Config) -> dict[str, Any]:
    f = _file(config)
    if not f.is_file():
        return {"done": {}}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"done": {}}
    if not isinstance(data, dict) or not isinstance(data.get("done"), dict):
        return {"done": {}}
    return data


def _save(config: Config, data: dict[str, Any]) -> None:
    f = _file(config)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")


def mark(config: Config, step: str) -> None:
    if step not in STEP_IDS:
        raise KeyError(step)
    with _lock:
        data = load(config)
        data["done"][step] = True
        _save(config, data)


def ci_enabled(root: Path) -> bool:
    wf = root / ".github" / "workflows"
    if not wf.is_dir():
        return False
    for f in [*wf.glob("*.yml"), *wf.glob("*.yaml")]:
        try:
            if _CI_RE.search(f.read_text(encoding="utf-8", errors="replace")):
                return True
        except OSError:
            continue
    return False


def detect(config: Config, check_demo: bool = True) -> dict[str, bool]:
    """Steps that can be seen from the outside right now."""
    found: dict[str, bool] = {}
    if check_demo:
        demo = config.agent(DEMO_AGENT_ID)
        found["demo_running"] = bool(demo and agent_reachable(demo.url))
    found["first_run"] = bool(db.list_runs(config.db_path, limit=1))
    found["agent_added"] = any(a.id != DEMO_AGENT_ID for a in config.agents)
    found["ci_enabled"] = ci_enabled(config.root)
    return found


def steps(config: Config, check_demo: bool = True) -> list[dict[str, Any]]:
    """[{id, title, done}]. A detected step is stored, so it stays done (a checklist does not un-check)."""
    with _lock:
        data = load(config)
        found = detect(config, check_demo)
        changed = False
        for k, v in found.items():
            if v and not data["done"].get(k):
                data["done"][k] = True
                changed = True
        if changed:
            _save(config, data)
    return [{"id": sid, "title": title, "done": bool(data["done"].get(sid))} for sid, title in STEPS]
