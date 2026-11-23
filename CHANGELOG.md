# Changelog

All notable changes to PanchoVoiceAI are in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version starts with `0.`, a minor version can change the scenario format, the API or the CLI. Read the notes before you upgrade.

## [Unreleased]

### Added

- ElevenLabs Agents adapter (`adapter: elevenlabs`). The caller talks to the agent over the ElevenLabs WebSocket API (signed URL with `ELEVENLABS_API_KEY`, any `pcm_*` or `ulaw_8000` audio format). Client tools are answered from the mock tool server, so tool calls and final state are checked as usual. After the call, server side (webhook) tool calls, the cost and the termination reason are read from the conversation record and saved as `platform.json`. `pv init` and `pv doctor` know the new adapter.
- Cloud voice providers. `tts.provider` accepts `elevenlabs` and `deepgram` (Aura), and `stt.provider` accepts `deepgram`. Keys come only from `ELEVENLABS_API_KEY` and `DEEPGRAM_API_KEY` (or the env var named in `api_key_env`). Scenario voices map to provider voices with `tts.voices` and `tts.default_voice`. `pv init`, `pv doctor` and the panel health check know the new providers.

### Changed

- `demo/docker-compose.yml` publishes Kokoro, Ollama and the demo agent on `127.0.0.1` only, so they are not reachable from the local network.

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
