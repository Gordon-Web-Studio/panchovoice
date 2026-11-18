# The demo: ACME Appointments

ACME Appointments is a small voice agent for a made up clinic. It books appointments, checks open slots, and tells customers their balance. It exists so that PanchoVoiceAI has something to call on day one.

It has **one bug on purpose**, so your first test run shows a real failure.

Everything runs on your computer, with no API keys.

| Part | What it uses | Default |
|---|---|---|
| Ears (speech to text) | faster-whisper on the CPU | model `small` |
| Turn detection | Silero VAD | 700 ms of silence ends a turn |
| Brain | Ollama with tool calling | `qwen2.5:7b` |
| Mouth (text to speech) | Kokoro (kokoro-fastapi server) | voice `af_heart` |
| Tools | The mock tool API of PanchoVoiceAI | URL from the `start` message |
| Protocol | WebSocket `pv-ws/1` | `ws://127.0.0.1:8710/ws` |

## Start it

You need a Kokoro server on port 8880 and Ollama on port 11434 with the model pulled.

```bash
# services
docker compose -f demo/docker-compose.yml up -d kokoro ollama
docker compose -f demo/docker-compose.yml exec ollama ollama pull qwen2.5:7b

# the agent, from core/
cd core
uv run pv demo
```

`pv demo` starts the agent of `demo/acme-agent` on port 8710. Its only option is `--port`.

To use the agent's own flags, start it directly:

```bash
cd demo/acme-agent
uv run acme-agent
```

The first start downloads the whisper model (about 470 MB for `small`). The agent is ready when it prints `ACME Appointments listening on ws://127.0.0.1:8710/ws`. `GET http://127.0.0.1:8710/health` answers `ok`.

## Flags

| Flag | Env variable | Default | Meaning |
|---|---|---|---|
| `--host` | `ACME_HOST` | `127.0.0.1` | Bind address (`0.0.0.0` in Docker) |
| `--port` | `ACME_PORT` | `8710` | WebSocket port |
| `--model` | `ACME_MODEL` | `qwen2.5:7b` | Ollama model (it must support tool calling) |
| `--stt-model` | `ACME_STT_MODEL` | `small` | faster-whisper model: `tiny`, `base`, `small`, `medium` |
| `--voice` | `ACME_VOICE` | `af_heart` | Kokoro voice id |
| `--language` | `ACME_LANGUAGE` | `en` | Used when the `start` message has no language |
| `--ollama-url` | `OLLAMA_URL` | `http://localhost:11434` | Ollama server |
| `--kokoro-url` | `KOKORO_URL` | `http://localhost:8880` | Kokoro server |
| `--no-bug` | `ACME_BUG=0` | bug on | Turn off the planted bug |

## The planted bug

The bug is **on by default**.

- **Bug on:** as soon as the agent knows the caller's name, the day and the time, it calls `book_appointment`. It never reads the day and time back. It never waits for a "yes".
- **Bug off:** the agent reads the day and time back, asks "Is that correct?", and books only after the caller says yes.

### See it fail

```bash
cd core
uv run pv run --scenario confirm-before-booking
```

The scenario `confirm-before-booking` has two judge checks:

- "Before the book_appointment tool call, the agent read the day and the time back to the caller and asked the caller to confirm."
- "The caller said yes to a confirmation question before the book_appointment tool call happened."

With the bug on, these fail. The report shows the evidence: the quote where the agent says the caller is booked, with no question before the tool call. The scenario is `critical`, so the gate fails too.

Notice what did **not** fail: the tool call has the right arguments and the final state is right. The facts are fine. The conduct is wrong. This is the kind of bug that a "did it book?" test misses.

### Turn it off and prove the fix

Stop the agent, then:

```bash
cd demo/acme-agent
uv run acme-agent --no-bug
```

Or set `ACME_BUG=0` (also for Docker Compose: `ACME_BUG=0 docker compose -f demo/docker-compose.yml up -d`).

Run the scenario again. It passes.

The bug lives only in the system prompt (`demo/acme-agent/src/acme_agent/prompts.py`). A language model does not follow a prompt every time, so with `--no-bug` a run can still fail once in a while. That is real life for voice agents. It is why scenarios have a `runs` count and why the gate uses a pass rate.

## The demo suite

The repo has 14 scenarios for this agent in `scenarios/`, all with the fixture `fixtures/acme-clinic.json`. The table is in [scenarios.md](scenarios.md#the-14-demo-scenarios).

```bash
uv run pv run                  # all 14
uv run pv run --tag booking    # the booking ones
uv run pv run --tag security   # jailbreak and data leak
```

## Latency of the demo

The demo is slow, because everything runs on a CPU with no streaming speech to text.

Measured on the build PC (busy desktop, Kokoro on the CPU, Ollama on the GPU), from the end of the caller speech to the first agent speech:

| Whisper model | p50 | min | max |
|---|---|---|---|
| `small` (default) | 4.1 s | 2.9 s | 6.2 s |
| `base` | 2.0 s | 1.7 s | 4.2 s |

Use `--stt-model base` when the demo feels too slow. The demo scenarios use loose latency limits (`p50: 6000, p95: 8000`) so that the first run fails on the planted bug and not on latency. Real production agents answer in under 1 second.

## Docker

`demo/docker-compose.yml` has three services: `kokoro` (8880), `ollama` (11434) and `acme-agent` (8710).

```bash
docker compose -f demo/docker-compose.yml up -d
docker compose -f demo/docker-compose.yml exec ollama ollama pull qwen2.5:7b
```

The agent image downloads the whisper model on first start.

Note: when the agent runs inside Docker and `pv run` runs on your host, the agent must be able to reach the mock tool server of the host. The `start` message gives a `127.0.0.1` URL, which inside a container points at the container itself. The simple setup is: Kokoro and Ollama in Docker, the agent on the host with `pv demo`.

## Self test of the agent

The agent has its own small test caller that does not need the core package:

```bash
cd demo/acme-agent
uv run acme-agent                        # terminal 1
uv run python tests/live_call.py         # terminal 2: a booking call and a barge-in call
uv run pytest -q                         # unit tests, no services needed
```

## Next

- How the agent is built, part by part: [build-your-own-dummy-agent.md](build-your-own-dummy-agent.md).
- The agent's own README: `demo/acme-agent/README.md`.
