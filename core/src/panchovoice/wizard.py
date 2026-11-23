"""`pv init`: a friendly step by step setup wizard.

Interactive by default (questionary + rich). Non interactive:
  pv init --yes                 take every default (actions like starting the demo are skipped)
  pv init --answers answers.json  take answers from a JSON file (missing keys use the defaults)

Answer keys: continue_anyway, provider, model, agent (demo | websocket), agent_id, agent_name, agent_url,
agent_language, overwrite, start_demo, smoke_test, build_panel, open_panel, skip_doctor.

Where it writes: `--config PATH`, else the cwd with `--here`, else the project root (the nearest folder with
scenarios/, or the source checkout root when run inside it).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from .config import CONFIG_NAME, parse_config_text
from .errors import ConfigError
from .probe import agent_health_text, find_web_out, ollama_models, repo_root

TOTAL = 7
ACTIONS = ("start_demo", "smoke_test", "build_panel", "open_panel")
SMOKE = ("book-happy-path", "confirm-before-booking")  # the second one holds the judge check the planted bug fails
DEMO_WAIT_S = 120  # how long to wait for a freshly started demo agent


class Asker:
    """One place for every question: answers file first, then --yes defaults, then a real prompt."""

    def __init__(self, answers: dict[str, Any] | None, yes: bool, console: Console):
        self.answers = answers or {}
        self.yes = yes
        self.interactive = not yes and answers is None
        self.console = console

    def _given(self, key: str) -> tuple[bool, Any]:
        return (key in self.answers, self.answers.get(key))

    def confirm(self, key: str, message: str, default: bool) -> bool:
        has, v = self._given(key)
        if has:
            return bool(v)
        if not self.interactive:
            if key in ACTIONS:
                return False  # never start processes without a person or an explicit answer
            return default
        import questionary

        r = questionary.confirm(message, default=default).ask()
        if r is None:
            raise KeyboardInterrupt
        return bool(r)

    def select(self, key: str, message: str, choices: list[tuple[str, str, bool]], default: str) -> str:
        """choices: (value, label, disabled)."""
        has, v = self._given(key)
        allowed = [c[0] for c in choices if not c[2]]
        if has:
            if v not in allowed:
                raise ValueError(f"answer {key}={v!r} is not one of {allowed}")
            return str(v)
        if not self.interactive:
            return default
        import questionary

        qs = [questionary.Choice(label, value=value, disabled="coming in phase 3" if dis else None)
              for value, label, dis in choices]
        r = questionary.select(message, choices=qs, default=next((c for c in qs if c.value == default), None)).ask()
        if r is None:
            raise KeyboardInterrupt
        return str(r)

    def text(self, key: str, message: str, default: str) -> str:
        has, v = self._given(key)
        if has:
            return str(v)
        if not self.interactive:
            return default
        import questionary

        r = questionary.text(message, default=default).ask()
        if r is None:
            raise KeyboardInterrupt
        return str(r).strip() or default


# --------------------------------------------------------------------------- yaml


VOICE_STACKS = {
    # key: (tts provider, stt provider)
    "local": ("kokoro", "faster-whisper"),
    "elevenlabs-deepgram": ("elevenlabs", "deepgram"),
    "deepgram": ("deepgram", "deepgram"),
}


def _voice_lines(tts_provider: str, stt_provider: str) -> list[str]:
    if tts_provider == "kokoro":
        lines = ["tts:", "  provider: kokoro", "  url: http://localhost:8880"]
    elif tts_provider == "elevenlabs":
        lines = [
            "tts:                             # the key comes from ELEVENLABS_API_KEY, never from this file",
            "  provider: elevenlabs",
            "  model: eleven_multilingual_v2",
            "  default_voice: JBFqnCBsd6RMkjVDRZzb   # ElevenLabs voice id for scenarios that name a Kokoro voice",
            "  # voices: { af_heart: <voice id>, em_alex: <voice id> }",
        ]
    else:
        lines = [
            "tts:                             # the key comes from DEEPGRAM_API_KEY, never from this file",
            "  provider: deepgram",
            "  default_voice: aura-2-thalia-en      # Deepgram Aura voice for scenarios that name a Kokoro voice",
            "  # voices: { af_heart: aura-2-thalia-en, em_alex: <aura voice> }",
        ]
    if stt_provider == "deepgram":
        lines += [
            "stt:                             # the key comes from DEEPGRAM_API_KEY, never from this file",
            "  provider: deepgram",
            "  model: nova-3",
        ]
    else:
        lines += ["stt:", "  provider: faster-whisper", "  model: small                   # tiny | base | small | medium"]
    return lines


def render_config(provider: str, model: str, agents: list[dict[str, str]], llm_url: str | None = None,
                  tts_provider: str = "kokoro", stt_provider: str = "faster-whisper") -> str:
    lines = [
        "# PanchoVoiceAI project config (written by `pv init`). Docs: docs/architecture/contract.md section 1",
        "version: 1",
        "data_dir: ./data                 # runs, recordings, sqlite db",
        "scenarios_dir: ./scenarios",
        "fixtures_dir: ./fixtures",
        "llm:                             # brain for the AI caller, the judge, and Panchito",
        f"  provider: {provider}",
        f"  model: {json.dumps(model)}",
    ]
    if llm_url:
        lines.append(f"  url: {llm_url}")
    lines += [
        "  budget_usd: 10                 # hard stop for the anthropic provider (estimated)",
        "  max_calls_per_run: 60",
    ]
    if provider == "anthropic":
        lines.insert(6, "  # the API key comes from the ANTHROPIC_API_KEY environment variable, never from this file")
    lines += _voice_lines(tts_provider, stt_provider)
    lines += ["agents:"]
    for a in agents:
        lines += [
            f"  - id: {a['id']}",
            f"    name: {json.dumps(a['name'])}",
            f"    adapter: {a.get('adapter', 'websocket')}",
        ]
        if a.get("adapter") == "elevenlabs":
            lines += [
                "    elevenlabs:                  # the key comes from ELEVENLABS_API_KEY, never from this file",
                f"      agent_id: {a['agent_id']}",
                "      # dynamic_variables: { customer_name: Jordan }",
            ]
        else:
            lines.append(f"    url: {a['url']}")
        lines.append(f"    language: {a['language']}")
    lines += ["gate:", "  pass_rate: 0.95", "  critical_must_pass_all: true", ""]
    return "\n".join(lines)


DEMO_AGENT = {"id": "acme-demo", "name": "ACME Appointments (demo)", "adapter": "websocket",
              "url": "ws://127.0.0.1:8710/ws", "language": "en"}


def copy_demo_files(target_root: Path, console: Console) -> list[str]:
    """Copy the demo scenarios and fixtures from the source checkout when the folders are missing or empty."""
    src = repo_root()
    done = []
    for name, pattern in (("scenarios", "*.yaml"), ("fixtures", "*.json")):
        dst = target_root / name
        src_dir = src / name
        if dst.resolve() == src_dir.resolve():
            continue
        dst.mkdir(parents=True, exist_ok=True)
        if any(dst.iterdir()):
            continue
        if not src_dir.is_dir():
            console.print(f"[yellow]No demo {name} found[/] next to the package; {dst} stays empty.")
            continue
        for f in sorted(src_dir.glob(pattern)):
            shutil.copy2(f, dst / f.name)
            done.append(f"{name}/{f.name}")
    return done


# --------------------------------------------------------------------------- main


def _step(console: Console, n: int, title: str) -> None:
    console.print()
    console.rule(f"[bold]Step {n} of {TOTAL}: {title}")


def _pv(config_file: Path, *args: str) -> list[str]:
    return [sys.executable, "-m", "panchovoice.cli", "--config", str(config_file), *args]


def _new_console_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_CONSOLE", 0)}
    return {"start_new_session": True}


def project_root(cwd: Path | None = None, here: bool = False) -> Path:
    """Where `pv init` writes: cwd with --here; else the nearest folder (cwd or a parent) with scenarios/;
    else the source checkout root when cwd is inside it; else cwd."""
    cur = (cwd or Path.cwd()).resolve()
    if here:
        return cur
    for d in [cur, *cur.parents]:
        if (d / "scenarios").is_dir():
            return d
    repo = repo_root().resolve()
    if cur == repo or repo in cur.parents:
        return repo
    return cur


def main(config_path: str | None = None, yes: bool = False, answers_file: str | None = None,
         console: Console | None = None, here: bool = False) -> int:
    console = console or Console(soft_wrap=True)
    answers = None
    if answers_file:
        try:
            answers = json.loads(Path(answers_file).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            console.print(f"[red]Cannot read the answers file:[/] {exc}")
            return 2
    if not yes and answers is None and not sys.stdin.isatty():
        console.print("[yellow]No terminal to ask questions in.[/] Use `pv init --yes` or `pv init --answers file.json`.")
        return 2
    ask = Asker(answers, yes, console)
    target = Path(config_path) if config_path else project_root(here=here) / CONFIG_NAME
    if target.is_dir():
        target = target / CONFIG_NAME
    target = target.resolve()
    root = target.parent
    try:
        return _wizard(ask, console, target, root)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/] Nothing more was changed. Run `pv init` again any time.")
        return 130
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        return 2


def _wizard(ask: Asker, console: Console, target: Path, root: Path) -> int:
    # 1) welcome
    _step(console, 1, "Welcome")
    console.print(Panel.fit(
        "Hi! PanchoVoiceAI tests voice AI agents with a real audio caller.\n"
        "This wizard will:\n"
        "  1. check your PC for what PanchoVoiceAI needs,\n"
        "  2. help you pick the brain (the LLM) and the agent to test,\n"
        f"  3. write {target.name} in {root},\n"
        "  4. offer to start the demo agent, run a first test and open the panel.\n"
        "Press Enter to take the default answer.", title="PanchoVoiceAI setup"))

    # 2) doctor
    _step(console, 2, "Check your PC")
    if ask.answers.get("skip_doctor"):
        console.print("Skipped (skip_doctor in the answers file).")
    else:
        from . import doctor
        from .config import Config

        cfg_now = Config()
        cfg_now._root = root
        checks = doctor.run_checks(cfg_now)
        doctor.print_table(checks, console)
        bad = doctor.failed_required(checks)
        if bad:
            console.print(f"[yellow]{len(bad)} required check(s) failed.[/] You can fix them later; the fix is in the table.")
            if not ask.confirm("continue_anyway", "Continue anyway?", True):
                console.print("OK. Fix the items above, then run `pv init` again.")
                return 1

    # 3) brain
    _step(console, 3, "Choose the brain")
    console.print("The brain plays the AI caller, judges the calls, and powers Panchito, your helper.")
    has_claude = shutil.which("claude") is not None
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    models = ollama_models() or []
    default_provider = "claude-cli" if has_claude else ("anthropic" if has_key else "ollama")
    provider = ask.select("provider", "Which brain?", [
        ("claude-cli", "Claude Code CLI, uses your Claude subscription" + (" (recommended, found on PATH)" if has_claude else " (not found on PATH)"), False),
        ("anthropic", "Anthropic API, reads ANTHROPIC_API_KEY from your environment", False),
        ("ollama", "Ollama, a free local model" + (f" ({len(models)} models found)" if models else " (not running now)"), False),
    ], default_provider)
    llm_url = None
    if provider == "claude-cli":
        model = ask.text("model", "Claude model (haiku is fast and cheap; sonnet or opus are stronger)", "haiku")
        if not has_claude:
            console.print("[yellow]`claude` is not on PATH yet.[/] Install Claude Code and log in before you run tests.")
    elif provider == "anthropic":
        model = ask.text("model", "Model (haiku, sonnet, opus or a full model id)", "haiku")
        if not has_key:
            console.print("[yellow]ANTHROPIC_API_KEY is not set.[/] Set it in your shell before you run tests. "
                          "It is never stored in the yaml.")
    else:
        default_model = "qwen2.5:7b" if ("qwen2.5:7b" in models or not models) else models[0]
        if models and ask.interactive:
            model = ask.select("model", "Which Ollama model?", [(m, m, False) for m in models], default_model)
        else:
            model = ask.text("model", "Ollama model name", default_model)
        if not models:
            console.print(f"[yellow]Ollama is not running now.[/] Start it and run `ollama pull {model}`.")

    # voice and ears of the harness
    console.print("The caller needs a voice (text to speech), and the harness needs ears (speech to text).")
    stack = ask.select("voice", "Which voice and speech to text?", [
        ("local", "Local and free: Kokoro (Docker) and faster-whisper, nothing leaves this PC", False),
        ("elevenlabs-deepgram", "Cloud: ElevenLabs voice, Deepgram speech to text (needs ELEVENLABS_API_KEY and DEEPGRAM_API_KEY)", False),
        ("deepgram", "Cloud, one account: Deepgram for both (needs DEEPGRAM_API_KEY)", False),
    ], "local")
    tts_provider, stt_provider = VOICE_STACKS[stack]
    for env in sorted({k for p, k in (("elevenlabs", "ELEVENLABS_API_KEY"), ("deepgram", "DEEPGRAM_API_KEY"))
                       if p in (tts_provider, stt_provider)}):
        if not os.environ.get(env):
            console.print(f"[yellow]{env} is not set.[/] Set it in your shell before you run tests. "
                          "It is never stored in the yaml.")

    # 4) agent
    _step(console, 4, "Choose what to test")
    kind = ask.select("agent", "Which agent do you want to test?", [
        ("demo", "The ACME demo agent (recommended first, runs on this PC)", False),
        ("websocket", "My own WebSocket agent (pv-ws/1 protocol)", False),
        ("elevenlabs", "An ElevenLabs Agents agent (needs ELEVENLABS_API_KEY for private agents)", False),
        ("http-turn", "An HTTP turn agent (coming in phase 3)", True),
    ], "demo")
    agents = [dict(DEMO_AGENT)]
    if kind == "websocket":
        url = ask.text("agent_url", "WebSocket url of your agent", "ws://localhost:9000/ws")
        if not url.startswith(("ws://", "wss://")):
            raise ValueError(f"the agent url must start with ws:// or wss:// (got {url!r})")
        lang = ask.select("agent_language", "Language of your agent", [("en", "English", False), ("es", "Spanish", False)], "en")
        aid = ask.text("agent_id", "A short id for your agent (kebab-case)", "my-agent")
        name = ask.text("agent_name", "A display name", "My voice agent")
        agents.append({"id": aid, "name": name, "adapter": "websocket", "url": url, "language": lang})
        console.print("The demo agent stays in the file too, so the demo scenarios keep working.")
    elif kind == "elevenlabs":
        el_id = ask.text("elevenlabs_agent_id", "ElevenLabs agent id (agent_..., from the agent page)", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", el_id or ""):
            raise ValueError(f"the ElevenLabs agent id looks wrong (got {el_id!r})")
        lang = ask.select("agent_language", "Language of your agent", [("en", "English", False), ("es", "Spanish", False)], "en")
        aid = ask.text("agent_id", "A short id for your agent (kebab-case)", "my-elevenlabs-agent")
        name = ask.text("agent_name", "A display name", "My ElevenLabs agent")
        agents.append({"id": aid, "name": name, "adapter": "elevenlabs", "agent_id": el_id, "language": lang})
        if not os.environ.get("ELEVENLABS_API_KEY"):
            console.print("[yellow]ELEVENLABS_API_KEY is not set.[/] Private agents and the tool call record need it.")
        console.print("Client tools are answered from the mock tool server. Webhook tools call your real backend.")

    # 5) write
    _step(console, 5, f"Write {target.name}")
    text = render_config(provider, model, agents, llm_url, tts_provider, stt_provider)
    try:
        parse_config_text(text, str(target))
    except ConfigError as exc:
        raise ValueError(f"the generated config is not valid: {exc}")
    console.print(Syntax(text, "yaml", theme="ansi_dark", word_wrap=True))
    write = True
    if target.exists():
        write = ask.confirm("overwrite", f"{target} already exists. Replace it?", False)
    if write:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        console.print(f"[green]Wrote[/] {target}")
    else:
        console.print(f"Kept the existing {target.name}.")
    copied = copy_demo_files(root, console)
    if copied:
        console.print(f"[green]Added[/] {len(copied)} demo files to scenarios/ and fixtures/.")

    # 6) actions
    _step(console, 6, "Try it")
    demo_up = agent_health_text(DEMO_AGENT["url"], 1.0) == "ok"
    started = []
    if demo_up:
        console.print("The demo agent is already running.")
    elif ask.confirm("start_demo", "Start the demo agent now (`pv demo` in a new window)?", True):
        subprocess.Popen(_pv(target, "demo"), cwd=str(root), **_new_console_kwargs())
        started.append("demo agent")
        console.print("Starting the demo agent (it loads its models, this can take about a minute)...")
        deadline = time.monotonic() + DEMO_WAIT_S
        while time.monotonic() < deadline and not demo_up:
            time.sleep(2)
            demo_up = agent_health_text(DEMO_AGENT["url"], 1.0) == "ok"
        console.print("[green]Demo agent is up.[/]" if demo_up else "[yellow]The demo agent is not up yet.[/] Look at its window.")
    if ask.confirm("smoke_test", "Run a first test now (book-happy-path and confirm-before-booking, about 2 minutes)?", demo_up):
        code = subprocess.call(_pv(target, "run", *[a for s in SMOKE for a in ("--scenario", s)]), cwd=str(root))
        console.print("Smoke test finished." if code == 0 else f"Smoke test ended with code {code}.")
    web_out = find_web_out()
    npm = shutil.which("npm")
    web_dir = repo_root() / "web"
    if web_out is None:
        if npm and (web_dir / "package.json").is_file():
            if ask.confirm("build_panel", "The panel is not built yet. Build it now (`npm install` then `npm run build` in web/, a few minutes)?", True):
                code = subprocess.call([npm, "install"], cwd=str(web_dir))
                if code == 0:
                    code = subprocess.call([npm, "run", "build"], cwd=str(web_dir))
                console.print("[green]Panel built.[/]" if code == 0 else f"[yellow]The panel build ended with code {code}.[/]")
        else:
            console.print(f"[yellow]The panel is not built.[/] Install Node.js, then run: cd {web_dir} && npm install && npm run build")
    if ask.confirm("open_panel", "Open the panel in your browser (`pv serve --open` in a new window)?", True):
        subprocess.Popen(_pv(target, "serve", "--open"), cwd=str(root), **_new_console_kwargs())
        started.append("panel server")
    if not ask.interactive:
        console.print("Non interactive mode: actions run only when the answers file asks for them.")

    # 7) next steps
    _step(console, 7, "Next steps")
    console.print(
        "  pv demo                     start the demo agent (port 8710)\n"
        "  pv run --scenario book-happy-path --scenario confirm-before-booking   run the smoke tests\n"
        "  pv serve --open             open the panel at http://127.0.0.1:8700\n"
        "  pv doctor                   check your PC again\n"
        "The first suite fails on purpose: the demo agent has a planted bug. Ask Panchito why!")
    return 0
