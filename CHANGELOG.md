# Changelog

All notable changes to PanchoVoiceAI are in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version starts with `0.`, a minor version can change the scenario format, the API or the CLI. Read the notes before you upgrade.

## [Unreleased]

### Added

- Demo scenario `book-happy-path-ai`: the happy path booking with an AI caller that reacts to the agent (it repeats and spells its name when asked), next to the scripted `book-happy-path`.

### Changed

- `book-happy-path` books for "James Hetfield" instead of "Sam Rivera". Deepgram Nova-3 heard "Rivera" as "Murdera" or "Samurdera" with the Aura voice in repeated runs, so the scripted caller, which cannot repeat or spell, failed for a speech to text reason instead of an agent bug.

## [0.1.0] - 2026-11-21

First public release.

### Added

- Engine: a simulated caller (scripted or AI) talks with real audio to the agent under test over the `pv-ws/1` WebSocket, and records both sides in a two channel file.
- Metrics: voice to voice latency P50 and P95, barge-in stop time, WER, tool calls with exact arguments, forbidden tools, call order, final state, `must_say`, `never_say`, and an LLM judge that quotes its evidence.
- Mock tool server driven by fixture JSON files.
- CI gate: `pv run --gate` with a pass rate threshold (default 95%), critical scenarios, and JUnit XML output.
- Demo agent "ACME Appointments" with a planted bug, and 14 demo scenarios (2 in Spanish).
- Web panel: home, runs with a two channel player, suites, scenario editor, fixtures, agents, settings, onboarding.
- Panchito: an AI helper that writes scenarios and fixtures and explains failed runs.
- `pv init` wizard and `pv doctor`.
- MCP server (`pv mcp`) with 10 tools. Every tool has a title and the four standard hints (read only, destructive, idempotent, open world).
- HTTP turn adapter for agents that work in turns over HTTP.
- LLM providers: `claude-cli`, `anthropic`, `ollama`.
- Docs: install, usage, configuration, scenarios, metrics, adapters, demo, build your own dummy agent, CI, Panchito, API, MCP, comparison, and a Spanish quick start.
- Repo: CI on every pull request, protected `main` branch, Dependabot, this changelog.

[Unreleased]: https://github.com/Gordon-Web-Studio/panchovoice/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Gordon-Web-Studio/panchovoice/releases/tag/v0.1.0
