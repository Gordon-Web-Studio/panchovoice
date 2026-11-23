# PanchoVoiceAI

**Automatic voice to voice tests for voice AI agents. Open source, self-hosted.**

A caller agent phones your voice agent with real audio. PanchoVoiceAI records both sides, then scores the call: response time, interruptions, words heard wrong, tool calls, final data, and conduct.

![Run detail: two channel player, turns, checks and tool calls](docs/images/run-detail.png)

## What it does

- **Real audio calls.** A caller with a text to speech voice talks to your agent. The caller follows a fixed script, or an AI plays a customer with a persona and a goal.
- **Two channel recording.** PanchoVoiceAI records the call itself: caller on the left channel, agent on the right. The evidence is the same for every platform.
- **Voice to voice latency.** P50, P95 and the slowest turn, measured on the recording.
- **Barge-in stop time.** The caller talks over the agent. The report shows how many milliseconds the agent needed to stop.
- **Word error rate (WER).** What the caller said against what the agent heard.
- **Tool calls with exact arguments.** Your agent calls a mock tool server. Every call is recorded. A scenario lists the expected calls, the forbidden calls and the order.
- **Final state.** The mock data after the call is compared with the expected data. Facts decide, not what the agent says.
- **LLM judge.** Yes or no questions about conduct. Each answer comes with a quote from the transcript as evidence.
- **CI gate.** A pass rate over all runs (default 95 percent), critical scenarios must pass every run, JUnit output, exit code 1 on failure.
- **Admin panel.** Runs, suites, scenarios, mock data, onboarding checklist.
- **Setup wizard.** `pv init` writes the config and checks what is missing.
- **Demo agent.** ACME Appointments, a small local voice agent with a bug planted on purpose, so the first run shows a real failure.
- **Panchito.** An AI companion that proposes scenarios and mock data and explains failures. Panchito proposes, you approve.

## How it works

```mermaid
flowchart LR
  S[Scenario YAML] --> C[Caller: scripted or AI, Kokoro voice]
  F[Fixture JSON: one source of data] --> C
  F --> M[Mock tool server]
  C <-- "audio, pv-ws/1" --> A[Agent under test]
  A -- "tool calls" --> M
  C --> R[Two channel recording]
  A --> R
  R --> X[Scores: latency, barge-in, WER]
  M --> Y[Scores: tool calls, final state]
  R --> J[LLM judge: yes or no with a quote]
  X --> O[Report, panel, JUnit, CI gate]
  Y --> O
  J --> O
```

1. A **scenario** file says who calls, what they say, and what must happen.
2. A **fixture** file holds the mock data. It feeds the mock tool server and the caller facts.
3. The **caller** speaks with a Kokoro voice. Your agent answers with audio.
4. Your agent calls its tools on the **mock tool server**. Each call is recorded.
5. PanchoVoiceAI **scores** the recording, the tool calls, and the transcript.
6. You read the result in the terminal, in the panel, or in CI.

## Quick start (about 5 minutes, plus model downloads)

You need:

- Python 3.12 with [uv](https://docs.astral.sh/uv/)
- Node 20 or newer (to build the panel)
- Docker (for the Kokoro voice server and Ollama)

```bash
git clone https://github.com/Gordon-Web-Studio/panchovoice.git panchovoice
cd panchovoice

# 1. voice server and local LLM for the demo agent
docker compose -f demo/docker-compose.yml up -d kokoro ollama
docker compose -f demo/docker-compose.yml exec ollama ollama pull qwen2.5:7b

# 2. the core engine
cd core
uv sync
uv run pv --config .. init   # setup wizard: checks your PC, writes panchovoice.yaml in the repo root

# 3. the demo agent (keep this terminal open)
uv run pv demo
```

The wizard also offers to start the demo agent, run a first test and open the panel for you. `--config ..` puts the config file in the repo root, next to `scenarios/` and `fixtures/`.

In a second terminal:

```bash
cd panchovoice/core
uv run pv run --scenario book-happy-path --scenario confirm-before-booking

# build the panel once, then open it
cd ../web && npm install && npm run build && cd ../core
uv run pv serve --open
```

The first run downloads speech models (several hundred MB). It is slow one time only.

Full steps for Windows, macOS and Linux: [docs/install.md](docs/install.md). En español: [docs/es/inicio-rapido.md](docs/es/inicio-rapido.md).

## What you will see

The demo agent has a bug on purpose: it books the appointment without reading the day and time back to the caller.

- `book-happy-path` checks the facts: the right tool calls and the right final data.
- `confirm-before-booking` asks the judge: "Before the book_appointment tool call, the agent read the day and the time back to the caller and asked the caller to confirm." With the bug on, this check **fails**, and the report shows the quote that proves it.

Restart the demo with the bug off and run again:

```bash
cd demo/acme-agent
uv run acme-agent --no-bug
```

Now the check passes. That is the loop: find a bug, fix it, prove the fix. More in [docs/demo.md](docs/demo.md).

Note: the demo agent runs on your CPU and is slow (about 2 to 5 seconds per reply). The demo scenarios use relaxed latency limits for that reason.

## Metrics

| Metric | What it tells you | How it is measured |
|---|---|---|
| Voice to voice latency (P50, P95, max) | How long the caller waits for a reply | From the recording: end of the caller audio (known exactly) to the first agent speech (Silero VAD) |
| Barge-in stop time | How fast the agent stops when the caller talks over it | From the recording: caller start to the end of the agent speech that was running |
| WER | How many words the agent heard wrong | Caller text against the agent's own transcript, with `jiwer`, same normalizer on both sides |
| Tool calls | Did the agent call the right tools with the right arguments | The mock tool server records every call |
| Final state | Is the data correct after the call | Mock data after the call against the expected rows |
| Phrases | Words the agent must say or must never say | Substring match on the agent transcript |
| Judge checks | Conduct: confirm, stay polite, refuse, no leaks | An LLM answers yes or no with an exact quote |

Details and limits: [docs/metrics.md](docs/metrics.md).

## Supported agents

**Today**

- Any agent that speaks the `pv-ws/1` WebSocket protocol. The protocol is small: PCM audio frames plus a few JSON messages. See [docs/adapters.md](docs/adapters.md).
- The ACME Appointments demo agent (in `demo/acme-agent/`).
- MCP server (`pv mcp`), so a coding agent such as Claude Code, Claude Desktop or Cursor can run suites and read results. See [docs/mcp.md](docs/mcp.md).

**Roadmap (planned, not built yet)**

- HTTP turn agents (one request per turn), for example in-house web assistants
- Pipecat and LiveKit agents
- Vapi, Retell and ElevenLabs agents
- Phone calls through Twilio
- A ready-made GitHub Action (today there is an example workflow, see [docs/ci.md](docs/ci.md))
- Webhooks, baseline comparison, red team suites

## How it compares

PanchoVoiceAI is self-hosted, works with any platform through adapters, records the neutral evidence itself, can run with a local brain, and has its own panel, wizard and AI companion. A fair comparison with commercial and open source tools, with sources: [docs/comparison.md](docs/comparison.md).

## Documentation

| Page | What is in it |
|---|---|
| [Install](docs/install.md) | Prerequisites per OS, Kokoro, Ollama, brain options, wizard, troubleshooting |
| [Usage](docs/usage.md) | Run suites, flags, results, panel tour, JUnit, exit codes |
| [Configuration](docs/configuration.md) | Every key of `panchovoice.yaml` |
| [Scenarios and fixtures](docs/scenarios.md) | Full reference, the 15 demo scenarios, tips |
| [Metrics](docs/metrics.md) | How each number is measured and why |
| [Adapters and pv-ws/1](docs/adapters.md) | Make your agent testable, platform roadmap |
| [Demo agent](docs/demo.md) | ACME Appointments and the planted bug |
| [Build your own dummy agent](docs/build-your-own-dummy-agent.md) | Ears, brain, mouth, tools, barge-in |
| [CI gate](docs/ci.md) | Gate rule, test levels, example GitHub Actions workflow |
| [Panchito](docs/panchito.md) | The AI companion |
| [API](docs/api.md) | REST API, mock tool API, connect other apps |
| [Comparison](docs/comparison.md) | Commercial and open source tools |
| [Inicio rápido (español)](docs/es/inicio-rapido.md) | Spanish quick start |

All pages: [docs/README.md](docs/README.md).

## Repository layout

| Folder | What it is |
|---|---|
| `core/` | Python engine, `pv` CLI, API server (package `panchovoice`) |
| `web/` | Admin panel (Next.js, static export) |
| `demo/acme-agent/` | The demo voice agent |
| `scenarios/`, `fixtures/` | The demo test suite |
| `docs/` | Documentation |

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md). Report security problems in private: [SECURITY.md](SECURITY.md).

## License

Apache License 2.0. See [LICENSE](LICENSE). Copyright 2026 Gordon Web Studio.
