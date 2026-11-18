from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

REPO = Path(__file__).resolve().parents[2]


def pytest_collection_modifyitems(config, items):
    if os.environ.get("PV_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set PV_LIVE=1 (needs Kokoro, claude CLI, models)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def repo() -> Path:
    return REPO


@pytest.fixture
def fixture_path(repo: Path) -> Path:
    return repo / "fixtures" / "acme-clinic.json"


@pytest.fixture
def project(tmp_path: Path, repo: Path):
    """A temp project: panchovoice.yaml + copy of the repo fixtures. Returns a function to build the config."""
    (tmp_path / "scenarios").mkdir()
    shutil.copytree(repo / "fixtures", tmp_path / "fixtures")

    def make(agent_url: str = "ws://127.0.0.1:1/ws", extra: str = "") -> "Config":  # noqa: F821
        from panchovoice.config import load_config

        (tmp_path / "panchovoice.yaml").write_text(
            "version: 1\n"
            "data_dir: ./data\nscenarios_dir: ./scenarios\nfixtures_dir: ./fixtures\n"
            "llm: {provider: ollama, model: none, max_calls_per_run: 10}\n"
            "agents:\n"
            f"  - {{id: fake, name: Fake agent, adapter: websocket, url: '{agent_url}', language: en}}\n"
            "gate: {pass_rate: 0.95, critical_must_pass_all: true}\n" + extra,
            encoding="utf-8",
        )
        return load_config(tmp_path / "panchovoice.yaml")

    make.root = tmp_path  # type: ignore[attr-defined]
    return make


@pytest.fixture
def fake_agent():
    from fake_agent import FakeAgent, FakeAgentConfig

    agents = []

    def start(**kw) -> FakeAgent:
        a = FakeAgent(FakeAgentConfig(**kw)).start()
        agents.append(a)
        return a

    yield start
    for a in agents:
        a.stop()
