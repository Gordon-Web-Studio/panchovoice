# Configuration: `panchovoice.yaml`

One file configures the project. `pv init` writes it. You can edit it by hand.

## Where the file is found

1. The path in `--config` (or `-c`), if you pass it. It may be the file or the folder that holds it.
2. The path in the env variable `PV_CONFIG`, if it is set.
3. `panchovoice.yaml` in the current folder, then in each parent folder.

With no file at all, PanchoVoiceAI uses the defaults below. In that case the project root is the nearest folder (current or parent) that has a `scenarios/` folder.

Relative paths in the file are relative to the folder of the file.

Unknown keys are errors. A typo in a key name stops the command with exit code 2 and shows the path of the key.

## Full example (all defaults)

```yaml
version: 1
data_dir: ./data
scenarios_dir: ./scenarios
fixtures_dir: ./fixtures
llm:
  provider: claude-cli
  model: haiku
  budget_usd: 10
  max_calls_per_run: 60
tts:
  provider: kokoro
  url: http://localhost:8880
stt:
  provider: faster-whisper
  model: small
agents:
  - id: acme-demo
    name: ACME Appointments (demo)
    adapter: websocket
    url: ws://localhost:8710/ws
    language: en
gate:
  pass_rate: 0.95
  critical_must_pass_all: true
```

## Top level keys

| Key | Type | Default | Meaning |
|---|---|---|---|
| `version` | integer | `1` | Version of the config format |
| `data_dir` | path | `./data` | Runs, recordings, the SQLite index, the voice cache |
| `scenarios_dir` | path | `./scenarios` | Folder with the scenario files |
| `fixtures_dir` | path | `./fixtures` | Folder with the fixture files |

Inside `data_dir`:

| Path | Content |
|---|---|
| `runs/<run_id>/` | `result.json`, `stereo.wav`, `caller.wav`, `agent.wav`, `transcript.json` |
| `panchovoice.db` | SQLite index of suites and runs |
| `cache/tts/` | Cached caller voice lines |

## `llm`: the brain

The brain plays the AI caller, answers the judge questions, and powers Panchito.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `llm.provider` | `claude-cli`, `anthropic`, `ollama` | `claude-cli` | Which brain to use |
| `llm.model` | string | `haiku` | `claude-cli`: `haiku`, `sonnet` or `opus`. `anthropic`: a model id, or the short names `haiku`, `sonnet`, `opus`. `ollama`: the model name, for example `qwen2.5:7b` |
| `llm.budget_usd` | number | `10` | Hard stop for the `anthropic` provider. The spend is an estimate from token counts |
| `llm.max_calls_per_run` | integer | `60` | Hard stop on the number of LLM calls in one run, for any provider |
| `llm.url` | string | not set | Base URL for `ollama`. When not set: `http://localhost:11434` |

Notes:

- `claude-cli` runs the `claude -p` command of Claude Code on your subscription. The cost estimate stays 0. Your personal Claude Code settings, hooks and plugins are kept out of these calls.
- `anthropic` reads the key from the env variable `ANTHROPIC_API_KEY`. There is no key in the config file. Never commit a key.
- When a limit is reached, the judge or the AI caller stops, and the affected run gets the status `error`, not `failed`.

## `tts`: the caller voice

| Key | Type | Default | Meaning |
|---|---|---|---|
| `tts.provider` | `kokoro`, `elevenlabs`, `deepgram` | `kokoro` | Text to speech engine. `kokoro` runs locally (Docker). `elevenlabs` and `deepgram` (Aura) are cloud APIs |
| `tts.url` | string | per provider | `http://localhost:8880`, `https://api.elevenlabs.io` or `https://api.deepgram.com` |
| `tts.model` | string | `eleven_multilingual_v2` for ElevenLabs | ElevenLabs model id. Not used by Kokoro or Deepgram (for Deepgram the voice is the model) |
| `tts.default_voice` | string | `JBFqnCBsd6RMkjVDRZzb` (ElevenLabs), `aura-2-thalia-en` (Deepgram) | Provider voice used when a scenario names a Kokoro voice (`af_heart`, ...) that is not in `tts.voices` |
| `tts.voices` | map | `{}` | Scenario voice to provider voice, for example `{af_heart: <ElevenLabs voice id>, em_alex: aura-2-orion-en}` |
| `tts.api_key_env` | env var name | `ELEVENLABS_API_KEY` or `DEEPGRAM_API_KEY` | Name of the environment variable that holds the key |

Voice rules for cloud providers: a scenario voice found in `tts.voices` is mapped; a Kokoro style id (`xx_name`) uses `tts.default_voice`; anything else is sent as is, so a scenario can name a provider voice directly.

Every caller line is cached in `data/cache/tts/` by provider, model, voice, speed and text, so a cloud provider is billed once per line, not once per run. Scripted lines are made before the call starts, so TTS time never counts as caller delay.

Example, cloud voice and ears:

```yaml
tts:
  provider: elevenlabs            # key in ELEVENLABS_API_KEY
  default_voice: JBFqnCBsd6RMkjVDRZzb
  voices: { af_heart: <voice id>, em_alex: <voice id> }
stt:
  provider: deepgram              # key in DEEPGRAM_API_KEY
  model: nova-3
```

## `stt`: speech to text inside the harness

| Key | Type | Default | Meaning |
|---|---|---|---|
| `stt.provider` | `faster-whisper`, `deepgram` | `faster-whisper` | Speech to text engine. `faster-whisper` runs on the CPU; `deepgram` is a cloud API |
| `stt.model` | string | `small` (whisper), `nova-3` (Deepgram) | Whisper: `tiny`, `base`, `small`, `medium`. Deepgram: any model name it accepts |
| `stt.url` | string | `https://api.deepgram.com` | Deepgram only |
| `stt.api_key_env` | env var name | `DEEPGRAM_API_KEY` | Deepgram only: name of the environment variable that holds the key |

API keys are read only from environment variables. A key in `panchovoice.yaml` is a config error. With a cloud provider, the caller lines and the agent audio are sent to that provider.

Voice activity detection (Silero) always runs locally, inside the Python process, so latency and barge-in are measured the same way with every provider.

The harness uses this model to transcribe the agent audio when the agent does not send its own `transcript` messages. WER is always computed from what the agent itself reports that it heard.

## `agents`: the agents under test

A list. Each scenario names one agent by `id`.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `agents[].id` | string | required | The id that scenarios use in `agent:` |
| `agents[].name` | string | empty | Display name |
| `agents[].adapter` | `websocket`, `http-turn`, `elevenlabs` | `websocket` | How the caller connects. `websocket` is the `pv-ws/1` protocol. `http-turn` is one HTTP request per turn. `elevenlabs` is an ElevenLabs Agents agent |
| `agents[].url` | string | required for `websocket` | For `websocket`: the WebSocket URL of the agent. Set for you with `elevenlabs` |
| `agents[].elevenlabs` | section | required for `elevenlabs` | `agent_id` (required), `api_key_env` (default `ELEVENLABS_API_KEY`), `dynamic_variables`, `environment`, `branch_id`, `fetch_conversation` (default true), `conversation_wait_s` (default 30), `api_url`. See [adapters.md](adapters.md#elevenlabs-agents) |
| `agents[].language` | `en`, `es` | `en` | Default language of the agent |

When the `agents` key is missing, the list holds the demo agent (`acme-demo` at `ws://localhost:8710/ws`).

Example with your own agent next to the demo:

```yaml
agents:
  - id: acme-demo
    name: ACME Appointments (demo)
    adapter: websocket
    url: ws://localhost:8710/ws
    language: en
  - id: my-agent
    name: My support agent
    adapter: websocket
    url: ws://localhost:9000/ws
    language: en
```

## `gate`: the CI gate

| Key | Type | Default | Meaning |
|---|---|---|---|
| `gate.pass_rate` | number from 0 to 1 | `0.95` | Minimum pass rate over all runs. Errors are excluded from the rate |
| `gate.critical_must_pass_all` | boolean | `true` | Every run of every `critical` scenario must pass |

Runs with harness errors always block the gate. See [ci.md](ci.md).

## Fixed values (not in the file)

| Thing | Value |
|---|---|
| API server and mock tools port | `8700` (`pv serve --port`, `pv run --mock-port`) |
| Demo agent port | `8710` (`pv demo --port`) |
| Panel dev server | `3700` |
| Audio format inside the engine | PCM signed 16 bit little endian, mono, 16000 Hz |
| Times in results | Milliseconds from call start (the moment the caller connects), as integers |
