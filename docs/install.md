# Install

PanchoVoiceAI runs on your own computer. Nothing is sent to a cloud service unless you choose a cloud brain.

## What you need

| Thing | Why | Version |
|---|---|---|
| Python with [uv](https://docs.astral.sh/uv/) | The core engine and the demo agent | Python 3.12 (the core needs 3.12, not 3.13) |
| Node.js | To build the admin panel | 20 or newer |
| Docker | To run the Kokoro voice server and Ollama | Any recent version |
| A brain (LLM) | For the AI caller, the judge and Panchito | See "Brain options" below |

Disk space: a few GB for the Docker images, the Ollama model and the speech models.

Ports used: `8700` (API server and mock tools), `8710` (demo agent), `3700` (panel in dev mode), `8880` (Kokoro), `11434` (Ollama).

## Prerequisites per OS

### Windows

```powershell
# uv (it can also install Python 3.12 for you)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv python install 3.12
```

- Install Node.js 20 or newer from [nodejs.org](https://nodejs.org/).
- Install [Docker Desktop](https://www.docker.com/products/docker-desktop/).
- Open a new terminal after each install, so the new commands are on `PATH`.

### macOS

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.12
```

- Install Node.js 20 or newer (from [nodejs.org](https://nodejs.org/) or with your package manager).
- Install [Docker Desktop](https://www.docker.com/products/docker-desktop/).

### Linux

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.12
```

- Install Node.js 20 or newer with your package manager.
- Install [Docker Engine](https://docs.docker.com/engine/install/).

## Kokoro (the caller voice)

The caller speaks with Kokoro, a local text to speech model. PanchoVoiceAI talks to it through an OpenAI compatible HTTP server.

```bash
docker run -d -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-cpu:latest
```

Or use the compose file of the repo, which starts Kokoro and Ollama together:

```bash
docker compose -f demo/docker-compose.yml up -d kokoro ollama
```

## Ollama (the brain of the demo agent)

The demo agent thinks with a local model in Ollama.

```bash
# if you run Ollama natively
ollama pull qwen2.5:7b

# if you use the compose file
docker compose -f demo/docker-compose.yml exec ollama ollama pull qwen2.5:7b
```

You need Ollama only for the demo agent, or when you choose `ollama` as the brain of PanchoVoiceAI.

## Brain options

The brain is the LLM that plays the AI caller, answers the judge questions, and powers Panchito. Set it in `panchovoice.yaml` under `llm` (see [configuration.md](configuration.md)).

| Provider | What it uses | Cost | Setup |
|---|---|---|---|
| `claude-cli` (default) | The `claude -p` command of Claude Code, on your Claude subscription | No extra spend | Install Claude Code and sign in. The `claude` command must be on `PATH` |
| `anthropic` | The Anthropic API | Pay per use, with a hard stop at `budget_usd` (estimated) | Set the env variable `ANTHROPIC_API_KEY` |
| `ollama` | A local model in Ollama | Free, fully local | `ollama pull <model>` and set `llm.model` to that name |

Example for the API key (never put the key in a file of the repo):

```bash
# macOS, Linux
export ANTHROPIC_API_KEY=your-key

# Windows PowerShell
$env:ANTHROPIC_API_KEY = "your-key"
```

Scripted scenarios with no `judge` checks need no brain at all.

## Install the core

```bash
git clone https://github.com/Gordon-Web-Studio/panchovoice.git panchovoice
cd panchovoice/core
uv sync
uv run pv --help
```

`pv` and `panchovoice` are the same command.

## The wizard: `pv init`

```bash
uv run pv --config .. init
```

The wizard is interactive. Press Enter to take the default answer. It has 7 steps:

| Step | What happens |
|---|---|
| 1. Welcome | It says what it will do |
| 2. Check your PC | It runs the `pv doctor` checks. If something required is missing, it shows the fix and asks if you want to continue |
| 3. The brain | You choose `claude-cli`, `anthropic` or `ollama`, and the model |
| 4. The agent | You choose the demo agent, or your own WebSocket agent (URL, language, id, name). The demo agent stays in the file too |
| 5. Write the config | It shows the file and writes `panchovoice.yaml`. If the file exists, it asks before it replaces it |
| 6. Try it | It offers to start the demo agent, to run a first test (`book-happy-path`), and to open the panel |
| 7. Next steps | It prints the commands to use next |

Where the file goes: the wizard writes `panchovoice.yaml` in the current folder, or in the folder you give with `--config`. From `core/`, `--config ..` puts it in the repo root, next to `scenarios/` and `fixtures/`. If the target folder has no scenarios or fixtures, the wizard copies the demo files there.

| Option | Meaning |
|---|---|
| `--yes`, `-y` | Take every default and ask nothing. The actions of step 6 are skipped |
| `--answers FILE` | Read the answers from a JSON file (for scripts and tests) |

You can edit `panchovoice.yaml` by hand at any time. See [configuration.md](configuration.md).

## Check your setup: `pv doctor`

```bash
uv run pv doctor
uv run pv doctor --json     # the same checks as JSON
```

`pv doctor` prints one line per check with PASS, WARN or FAIL, and a fix for each problem. Checks marked with `*` are required. It checks: Python, uv, Docker, the Kokoro voice server, Ollama, the brain, faster-whisper, the demo agent, the panel build, ports 8700 and 8710, and free disk space. Run it when something does not work.

## Build the panel

The panel is a static site. Build it once. `pv serve` then serves it.

```bash
cd web
npm install
npm run build        # writes web/out/
cd ../core
uv run pv serve --open
```

## Troubleshooting

**A port is in use.**
`pv run` starts its mock tool server on port `8700`. If the port is busy (for example `pv serve` is running), it picks a free port by itself. You can also choose one: `pv run --mock-port 8701`. For the server: `pv serve --port 8701`. For the demo agent: `pv demo --port 8711` (then change the agent `url` in `panchovoice.yaml`).

**Low memory, or the demo agent is very slow.**
Use a smaller speech model. For the demo agent: `uv run acme-agent --stt-model base` (in `demo/acme-agent`). For the core, set `stt.model: base` in `panchovoice.yaml`. On the build PC, `base` cut the demo latency from about 4 seconds to about 2 seconds.

**The first run is slow.**
The first run downloads the whisper model (about 470 MB for `small`) and loads the Ollama model. This happens one time. The caller voice lines are also cached in `data/cache/tts`, so later runs start faster.

**"the `claude` command is not on PATH".**
You chose `claude-cli` but Claude Code is not installed, or the terminal cannot find it. Install it, sign in, and open a new terminal. Or change `llm.provider`.

**"Kokoro TTS not reachable".**
The voice server is not running. Start it (see above) and check `http://localhost:8880` in a browser.

**A run shows `error`, not `failed`.**
`error` means the harness could not do the test (agent down, judge down). It is not a failure of your agent. Read the `error` and `notes` fields of the result, fix the setup, and run again.

**The panel shows a "Demo data" badge.**
The panel cannot reach the API at `http://localhost:8700`, so it shows sample data. Start `pv serve`.

**Windows: slow tool calls.**
Use `127.0.0.1`, not `localhost`, when your agent calls local HTTP services. On Windows, `localhost` can try IPv6 first and wait about 2 seconds per request.
