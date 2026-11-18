"""`panchovoice` / `pv` command line (contract section 8)."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from . import __version__
from .config import Config, load_config
from .errors import ConfigError

app = typer.Typer(help="PanchoVoiceAI: test voice AI agents with a real audio caller.", no_args_is_help=True,
                  add_completion=False)
console = Console(soft_wrap=True, width=None if sys.stdout.isatty() else 150)
_state: dict[str, Optional[str]] = {"config": None}


def _version(value: bool) -> None:
    if value:
        console.print(f"panchovoice {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to panchovoice.yaml (default: search cwd and parents)."),
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True, help="Show the version."),
) -> None:
    _state["config"] = config


def _cfg() -> Config:
    try:
        return load_config(_state["config"])
    except ConfigError as exc:
        console.print(f"[red]Config error[/] in {exc.source}:")
        for e in exc.errors:
            console.print(f"  {e.path or '(file)'}: {e.message}")
        raise typer.Exit(2)


# --------------------------------------------------------------------------- validate


@app.command()
def validate(paths: list[Path] = typer.Argument(None, help="Files or folders (default: scenarios_dir and fixtures_dir).")) -> None:
    """Validate scenario (.yaml) and fixture (.json) files."""
    from .validate import validate_paths

    cfg = _cfg()
    reports = validate_paths(list(paths or []), cfg)
    if not reports:
        console.print(f"[yellow]No files found[/] in {cfg.scenarios_path} or {cfg.fixtures_path}")
        raise typer.Exit(1)
    bad = 0
    for r in reports:
        if r.ok:
            console.print(f"[green]ok[/]    {r.kind:8} {r.path}")
        else:
            bad += 1
            console.print(f"[red]error[/] {r.kind:8} {r.path}")
            for e in r.errors:
                console.print(f"        {e.path or '(file)'}: {e.message}")
    console.print(f"{len(reports) - bad} valid, {bad} invalid")
    raise typer.Exit(1 if bad else 0)


# --------------------------------------------------------------------------- run


@app.command()
def run(
    scenario: list[str] = typer.Option(None, "--scenario", "-s", help="Scenario id (repeatable)."),
    tag: list[str] = typer.Option(None, "--tag", "-t", help="Only scenarios with this tag (repeatable)."),
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Only scenarios for this agent id."),
    runs: Optional[int] = typer.Option(None, "--runs", "-n", min=1, help="Repeat each scenario N times (default: the scenario's runs)."),
    junit: Optional[Path] = typer.Option(None, "--junit", help="Write JUnit XML here."),
    json_out: Optional[Path] = typer.Option(None, "--json", help="Write the suite summary and results as JSON here."),
    gate_flag: bool = typer.Option(False, "--gate", help="Exit with code 1 when the gate fails."),
    port: int = typer.Option(8700, "--mock-port", help="Port of the mock tools server (a free port is used if busy)."),
) -> None:
    """Run a suite of scenarios against the agent(s) and score them."""
    from .report import junit_xml, print_summary
    from .runner import ensure_mock_server, run_suite, select_scenarios

    cfg = _cfg()
    try:
        chosen, invalid = select_scenarios(cfg, list(scenario or []) or None, list(tag or []) or None, agent)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(2)
    for name in sorted(invalid):
        console.print(f"[yellow]skipped invalid file[/] {name} (run `pv validate`)")
    if not chosen:
        console.print("[yellow]No scenarios match.[/]")
        raise typer.Exit(2)
    base = ensure_mock_server(port)
    console.print(f"mock tools at {base}; data in {cfg.data_path}")

    def progress(e: dict) -> None:
        t = e.get("type")
        if t == "run_started":
            console.print(f"[bold]>[/] {e['scenario_id']} ({e['index'] + 1}/{e['total']})")
        elif t == "caller_turn":
            console.print(f"    caller: {e['text']}", highlight=False)
        elif t == "agent_turn":
            console.print(f"    agent:  {e['text'][:160]}", highlight=False)
        elif t == "run_finished":
            color = {"passed": "green", "failed": "red"}.get(e["status"], "yellow")
            console.print(f"  [{color}]{e['status']}[/] {e['run_id']}")

    suite = run_suite(cfg, scenarios=chosen, runs=runs, on_progress=progress, agent_id=agent, mock_base_url=base)
    results = suite.pop("results")
    print_summary(suite, results, console)
    if junit:
        junit.parent.mkdir(parents=True, exist_ok=True)
        junit.write_text(junit_xml(suite, results), encoding="utf-8")
        console.print(f"JUnit XML: {junit}")
    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps({"suite": suite, "results": results}, indent=2, ensure_ascii=False), encoding="utf-8")
        console.print(f"JSON: {json_out}")
    if gate_flag and not suite.get("gate_passed"):
        raise typer.Exit(1)


# --------------------------------------------------------------------------- demo


@app.command()
def demo(
    port: int = typer.Option(8710, "--port", help="Port for the demo agent."),
    stt_model: Optional[str] = typer.Option(None, "--stt-model", help="faster-whisper model of the demo agent (default base, about 2x faster than small; ACME_STT_MODEL is respected)."),
) -> None:
    """Start the ACME demo agent (demo/acme-agent) on port 8710."""
    cfg = _cfg()
    candidates = [cfg.root / "demo" / "acme-agent", Path(__file__).resolve().parents[3] / "demo" / "acme-agent"]
    proj = next((p for p in candidates if (p / "pyproject.toml").is_file()), None)
    if proj is None:
        console.print("[red]The demo agent is not here.[/] Expected demo/acme-agent/pyproject.toml next to panchovoice.yaml.")
        raise typer.Exit(1)
    uv = shutil.which("uv")
    if not uv:
        console.print("[red]`uv` is not on PATH.[/] Install it from https://docs.astral.sh/uv/")
        raise typer.Exit(1)
    cmd = [uv, "run", "--project", str(proj), "acme-agent", "--port", str(port)]
    model = stt_model or (None if os.environ.get("ACME_STT_MODEL") else "base")
    if model:
        cmd += ["--stt-model", model]
    console.print(f"starting demo agent: {' '.join(cmd)}")
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)  # do not let the core venv leak into the demo project
    try:
        raise typer.Exit(subprocess.call(cmd, cwd=str(proj), env=env))
    except KeyboardInterrupt:
        raise typer.Exit(0)


# --------------------------------------------------------------------------- lazy commands


def _lazy(module: str, func: str, *args, **kwargs) -> None:
    try:
        mod = importlib.import_module(f"panchovoice.{module}")
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("panchovoice"):
            console.print(f"[yellow]`{module}` is not installed yet.[/]")
            raise typer.Exit(1)
        raise
    fn = getattr(mod, func, None)
    if fn is None:
        console.print(f"[yellow]panchovoice.{module} has no {func}() yet.[/]")
        raise typer.Exit(1)
    code = fn(*args, **kwargs)
    raise typer.Exit(int(code or 0))


@app.command()
def init(
    yes: bool = typer.Option(False, "--yes", "-y", help="Take every default, ask nothing (actions are skipped)."),
    answers: Optional[Path] = typer.Option(None, "--answers", help="JSON file with the answers (for scripts and tests)."),
    here: bool = typer.Option(False, "--here", help="Write panchovoice.yaml in the current folder (your own project)."),
) -> None:
    """Interactive setup wizard (writes panchovoice.yaml at the project root)."""
    _lazy("wizard", "main", _state["config"], yes=yes, answers_file=str(answers) if answers else None, here=here)


@app.command()
def doctor(json_out: bool = typer.Option(False, "--json", help="Print the checks as JSON.")) -> None:
    """Check prerequisites and print fixes."""
    _lazy("doctor", "main", _state["config"], as_json=json_out)


@app.command()
def serve(port: int = typer.Option(8700, "--port"), open_browser: bool = typer.Option(False, "--open")) -> None:
    """API server plus the built panel."""
    _lazy("server", "serve", port=port, open_browser=open_browser, config_path=_state["config"])


@app.command()
def mcp(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to panchovoice.yaml (overrides the global --config)."),
    mock_port: int = typer.Option(0, "--mock-port", help="Port of the mock tools server for run_suite (0: a free port)."),
) -> None:
    """MCP server over stdio, so a coding agent can run suites and read results (see docs/mcp.md)."""
    _lazy("mcp_server", "main", config or _state["config"], mock_port=mock_port)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
