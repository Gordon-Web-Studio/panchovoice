"""`pv init` (--yes, --answers), `pv doctor --json`, and the `pv demo` default STT model."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from panchovoice import doctor, wizard
from panchovoice.cli import app
from panchovoice.config import load_config


@pytest.fixture
def quiet(monkeypatch):
    """No network or processes: doctor, Ollama and the demo probe are stubbed."""
    monkeypatch.setattr(doctor, "run_checks", lambda cfg: [])
    monkeypatch.setattr(wizard, "ollama_models", lambda *a, **k: [])
    monkeypatch.setattr(wizard, "agent_health_text", lambda *a, **k: None)
    popen = []
    monkeypatch.setattr(wizard.subprocess, "Popen", lambda *a, **k: popen.append(a))
    monkeypatch.setattr(wizard.subprocess, "call", lambda *a, **k: popen.append(a) or 0)
    monkeypatch.setattr(wizard, "DEMO_WAIT_S", 0)
    return popen


def test_init_yes_defaults(tmp_path, monkeypatch, quiet):
    monkeypatch.setattr(wizard.shutil, "which", lambda name: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    target = tmp_path / "panchovoice.yaml"
    r = CliRunner().invoke(app, ["--config", str(target), "init", "--yes"])
    assert r.exit_code == 0, r.output
    cfg = load_config(target)
    assert cfg.llm.provider == "ollama" and cfg.llm.model == "qwen2.5:7b"
    assert [a.id for a in cfg.agents] == ["acme-demo"]
    assert (tmp_path / "scenarios" / "book-happy-path.yaml").is_file()
    assert (tmp_path / "fixtures" / "acme-clinic.json").is_file()
    assert quiet == []  # --yes never starts processes
    assert "Step 7 of 7" in r.output
    # a second --yes keeps the existing file
    target.write_text(target.read_text(encoding="utf-8") + "# mine\n", encoding="utf-8")
    r2 = CliRunner().invoke(app, ["--config", str(target), "init", "--yes"])
    assert r2.exit_code == 0 and target.read_text(encoding="utf-8").endswith("# mine\n")


def test_init_answers_websocket_agent(tmp_path, monkeypatch, quiet):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-key")
    target = tmp_path / "panchovoice.yaml"
    target.write_text("version: 1\n", encoding="utf-8")
    answers = {"provider": "anthropic", "model": "sonnet", "agent": "websocket", "agent_url": "ws://127.0.0.1:9001/ws",
               "agent_language": "es", "agent_id": "my-bot", "agent_name": "My bot", "overwrite": True,
               "smoke_test": False}
    af = tmp_path / "answers.json"
    af.write_text(json.dumps(answers), encoding="utf-8")
    r = CliRunner().invoke(app, ["--config", str(target), "init", "--answers", str(af)])
    assert r.exit_code == 0, r.output
    text = target.read_text(encoding="utf-8")
    assert "test-not-a-key" not in text and "ANTHROPIC_API_KEY" in text  # a comment, never the key
    cfg = load_config(target)
    assert cfg.llm.provider == "anthropic" and cfg.llm.model == "sonnet"
    assert [(a.id, a.language) for a in cfg.agents] == [("acme-demo", "en"), ("my-bot", "es")]
    # the demo scenarios still validate against the new config
    v = CliRunner().invoke(app, ["--config", str(target), "validate"])
    assert v.exit_code == 0, v.output


def test_init_answers_actions_and_errors(tmp_path, quiet):
    af = tmp_path / "a.json"
    af.write_text(json.dumps({"provider": "claude-cli", "start_demo": True, "open_panel": True}), encoding="utf-8")
    r = CliRunner().invoke(app, ["--config", str(tmp_path / "panchovoice.yaml"), "init", "--answers", str(af)])
    assert r.exit_code == 0, r.output
    cmds = [" ".join(map(str, a[0])) for a in quiet]
    assert any(c.endswith(" demo") for c in cmds) and any(c.endswith("serve --open") for c in cmds)
    af.write_text(json.dumps({"agent": "http-turn"}), encoding="utf-8")
    bad = CliRunner().invoke(app, ["--config", str(tmp_path / "x.yaml"), "init", "--answers", str(af)])
    assert bad.exit_code == 2 and "not one of" in bad.output


def test_project_root_rules(tmp_path):
    (tmp_path / "proj" / "scenarios").mkdir(parents=True)
    sub = tmp_path / "proj" / "core"
    sub.mkdir()
    assert wizard.project_root(sub) == (tmp_path / "proj").resolve()
    assert wizard.project_root(sub, here=True) == sub.resolve()
    repo = wizard.repo_root()
    assert wizard.project_root(repo / "core") == repo.resolve()  # never core/ inside the checkout
    lone = tmp_path / "lone"
    lone.mkdir()
    assert wizard.project_root(lone) == lone.resolve()


def test_init_writes_at_project_root_not_cwd(tmp_path, monkeypatch, quiet):
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "scenarios" / "mine.yaml").write_text("id: mine\n", encoding="utf-8")
    sub = tmp_path / "core"
    sub.mkdir()
    monkeypatch.chdir(sub)
    monkeypatch.delenv("PV_CONFIG", raising=False)
    r = CliRunner().invoke(app, ["init", "--yes"])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "panchovoice.yaml").is_file() and not (sub / "panchovoice.yaml").exists()
    assert not (sub / "scenarios").exists()
    assert [p.name for p in (tmp_path / "scenarios").iterdir()] == ["mine.yaml"]  # not empty: nothing copied
    r2 = CliRunner().invoke(app, ["init", "--yes", "--here"])
    assert r2.exit_code == 0 and (sub / "panchovoice.yaml").is_file()


def test_init_smoke_runs_both_and_offers_panel_build(tmp_path, monkeypatch, quiet):
    monkeypatch.setattr(wizard, "find_web_out", lambda *a: None)
    monkeypatch.setattr(wizard.shutil, "which", lambda name: f"/bin/{name}")
    af = tmp_path / "a.json"
    af.write_text(json.dumps({"smoke_test": True, "build_panel": True}), encoding="utf-8")
    r = CliRunner().invoke(app, ["--config", str(tmp_path / "panchovoice.yaml"), "init", "--answers", str(af)])
    assert r.exit_code == 0, r.output
    cmds = [" ".join(map(str, a[0])) for a in quiet]
    assert any("--scenario book-happy-path --scenario confirm-before-booking" in c for c in cmds)
    assert "/bin/npm install" in cmds and "/bin/npm run build" in cmds
    quiet.clear()
    CliRunner().invoke(app, ["--config", str(tmp_path / "panchovoice.yaml"), "init", "--yes"])
    assert quiet == []  # --yes builds nothing


def test_doctor_json_shape():
    r = CliRunner().invoke(app, ["doctor", "--json"])
    assert r.exit_code in (0, 1), r.output
    data = json.loads(r.output)
    assert set(data) == {"ok", "checks", "summary"}
    ids = {c["id"] for c in data["checks"]}
    assert {"python", "uv", "docker", "tts", "ollama", "llm", "stt", "demo_agent", "web", "port_8700", "port_8710",
            "disk"} <= ids
    for c in data["checks"]:
        assert c["status"] in ("PASS", "WARN", "FAIL") and set(c) == {"id", "name", "status", "detail", "fix", "required"}
        if c["status"] != "PASS":
            assert c["fix"]
    assert (r.exit_code == 1) == any(c["required"] and c["status"] == "FAIL" for c in data["checks"])


def test_doctor_exit_code_on_required_fail(monkeypatch):
    monkeypatch.setattr(doctor, "run_checks", lambda cfg: [
        doctor.Check("tts", "Kokoro TTS", "FAIL", "down", "start it", True),
        doctor.Check("docker", "Docker", "WARN", "off", "start it", False)])
    r = CliRunner().invoke(app, ["doctor"])
    assert r.exit_code == 1 and "FAIL" in r.output and "start it" in r.output
    monkeypatch.setattr(doctor, "run_checks", lambda cfg: [doctor.Check("docker", "Docker", "WARN", "off", "x", False)])
    assert CliRunner().invoke(app, ["doctor"]).exit_code == 0


def test_demo_default_stt_model(monkeypatch):
    import panchovoice.cli as cli

    seen = []
    monkeypatch.delenv("ACME_STT_MODEL", raising=False)
    monkeypatch.setattr(cli.shutil, "which", lambda n: "uv")
    monkeypatch.setattr(cli.subprocess, "call", lambda cmd, **k: seen.append(cmd) or 0)
    assert CliRunner().invoke(app, ["demo"]).exit_code == 0
    assert seen[-1][-2:] == ["--stt-model", "base"]
    assert CliRunner().invoke(app, ["demo", "--stt-model", "small"]).exit_code == 0
    assert seen[-1][-2:] == ["--stt-model", "small"]
    monkeypatch.setenv("ACME_STT_MODEL", "tiny")
    CliRunner().invoke(app, ["demo"])
    assert "--stt-model" not in seen[-1]
