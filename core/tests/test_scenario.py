from __future__ import annotations

from pathlib import Path

import pytest

from panchovoice.config import load_config
from panchovoice.fixture import validate_fixture_text
from panchovoice.scenario import check_scenario_refs, list_scenario_files, validate_scenario_text
from panchovoice.validate import validate_paths

GOOD = """
id: demo-one
title: A good scenario
agent: acme-demo
fixture: acme-clinic.json
caller:
  mode: scripted
  turns:
    - say: "Hi."
      interrupt_after_ms: 500
    - say: "Bye."
      pause_ms: 2000
expect:
  tool_calls: [{name: find_slot, args: {day: tuesday}}]
  latency_ms: {p50: 1500}
"""


def _paths(errors):
    return {e.path: e.message for e in errors}


def test_repo_scenarios_and_fixtures_are_valid(repo: Path):
    cfg = load_config(repo / "panchovoice.yaml") if (repo / "panchovoice.yaml").exists() else load_config(start=repo)
    reports = validate_paths([], cfg)
    assert len([r for r in reports if r.kind == "scenario"]) >= 12
    bad = {str(r.path): [str(e) for e in r.errors] for r in reports if not r.ok}
    assert not bad, bad


def test_repo_has_two_spanish_and_one_ai_scenario(repo: Path):
    texts = [p.read_text(encoding="utf-8") for p in list_scenario_files(repo / "scenarios")]
    assert sum("language: es" in t for t in texts) >= 2
    assert any("mode: ai" in t for t in texts)
    assert any("interrupt_after_ms" in t for t in texts)


def test_good_scenario():
    scn, errors = validate_scenario_text(GOOD, "demo-one.yaml")
    assert errors == []
    assert scn is not None and scn.has_interrupts
    assert scn.caller.turns[1].pause_ms == 2000
    assert scn.limits.max_turns == 12 and scn.runs == 1


def test_unknown_key_has_dotted_path():
    text = GOOD.replace("    - say: \"Bye.\"", "    - say: \"Bye.\"\n      shout: true")
    _, errors = validate_scenario_text(text, "demo-one.yaml")
    assert _paths(errors) == {"caller.turns.1.shout": "unknown key"}


def test_unknown_top_level_key():
    _, errors = validate_scenario_text(GOOD + "colour: red\n")
    assert "colour" in _paths(errors)


def test_scripted_needs_turns():
    text = GOOD.split("caller:")[0] + "caller:\n  mode: scripted\n"
    _, errors = validate_scenario_text(text)
    assert any(e.path == "caller" and "turns" in e.message for e in errors)


def test_ai_needs_persona_and_goal():
    text = GOOD.split("caller:")[0] + "caller:\n  mode: ai\n  persona: someone\n"
    _, errors = validate_scenario_text(text)
    assert any(e.path == "caller" and "goal" in e.message for e in errors)


def test_bad_enum_and_id():
    text = GOOD.replace("id: demo-one", "id: Demo_One").replace("agent: acme-demo", "agent: acme-demo\nrisk: high")
    _, errors = validate_scenario_text(text)
    p = _paths(errors)
    assert "id" in p and "risk" in p


def test_id_must_match_file_name():
    _, errors = validate_scenario_text(GOOD, "other-name.yaml")
    assert _paths(errors).keys() == {"id"}


def test_invalid_yaml():
    _, errors = validate_scenario_text("id: [unclosed")
    assert errors and "invalid YAML" in errors[0].message


def test_refs_agent_fixture_and_tool_names(repo: Path):
    cfg = load_config(start=repo)
    text = GOOD.replace("agent: acme-demo", "agent: nobody").replace("name: find_slot", "name: fly_to_moon")
    scn, errors = validate_scenario_text(text)
    assert not errors
    refs = _paths(check_scenario_refs(scn, cfg))
    assert "agent" in refs and "expect.tool_calls.0.name" in refs
    scn2, _ = validate_scenario_text(GOOD.replace("acme-clinic.json", "missing.json"))
    assert "fixture" in _paths(check_scenario_refs(scn2, cfg))


def test_validate_cli_reports_path(tmp_path: Path, repo: Path):
    from typer.testing import CliRunner

    from panchovoice.cli import app

    bad = tmp_path / "demo-one.yaml"
    bad.write_text(GOOD + "surprise: 1\n", encoding="utf-8")
    good = tmp_path / "demo-two.yaml"
    good.write_text(GOOD.replace("demo-one", "demo-two"), encoding="utf-8")
    args = ["--config", str(repo / "panchovoice.yaml")] if (repo / "panchovoice.yaml").exists() else []
    r = CliRunner().invoke(app, [*args, "validate", str(bad), str(good)])
    assert r.exit_code == 1
    assert "surprise: unknown key" in r.output
    assert "1 valid, 1 invalid" in r.output


# ------------------------------------------------------------------ fixtures


def test_fixture_good(fixture_path: Path):
    fx, errors = validate_fixture_text(fixture_path.read_text(encoding="utf-8"), fixture_path.name)
    assert errors == [] and fx is not None
    assert fx.tool("find_slot").action == "find"


@pytest.mark.parametrize("mutation,path", [
    (lambda d: d["tools"][0].update(color="red"), "tools.0.color"),
    (lambda d: d["tools"][0].update(action="explode"), "tools.0.action"),
    (lambda d: d["tools"][0].update(match=["nope"]), "tools.0"),
    (lambda d: d.update(extra=1), "extra"),
])
def test_fixture_bad(fixture_path: Path, mutation, path):
    import json

    d = json.loads(fixture_path.read_text(encoding="utf-8"))
    mutation(d)
    _, errors = validate_fixture_text(json.dumps(d))
    assert path in _paths(errors)
