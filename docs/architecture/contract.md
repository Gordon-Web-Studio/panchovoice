# PanchoVoiceAI contract (v1)

This file is the shared contract between the four parts of the repo. Every part MUST follow it. Change it only with a note in `docs/architecture/decisions.md`.

| Part | Folder | Language | Owner of |
|---|---|---|---|
| Core engine + API server + CLI + wizard | `core/` | Python 3.12, package `panchovoice` | runner, caller, metrics, mock tools, REST API, Panchito backend, MCP (later) |
| Demo agent "ACME Appointments" | `demo/acme-agent/` | Python 3.12, package `acme_agent` | a small voice agent to test against |
| Admin panel | `web/` | Next.js (App Router, TypeScript, Tailwind), static export | all UI, onboarding, Panchito chat UI |
| Scenarios and fixtures | `scenarios/`, `fixtures/` | YAML, JSON | the demo test suite |

Rules for every part:
- No real client names, no secrets, no internal company data. Example names: ACME, Sam Rivera, Jordan Lee.
- No em dash or en dash characters anywhere (code, comments, docs). Use commas, colons, parentheses.
- Ports: API server `8700`, demo agent `8710`, Next.js dev `3700`, Kokoro TTS `8880`, Ollama `11434`.
- All times in the run result are milliseconds from call start (`t0` = the moment the caller connects), as integers.
- Audio inside the engine: PCM signed 16 bit little endian, mono, 16000 Hz.

---

## 1. Project config file: `panchovoice.yaml` (repo or working dir root)

```yaml
version: 1
data_dir: ./data                 # runs, recordings, sqlite db
scenarios_dir: ./scenarios
fixtures_dir: ./fixtures
llm:                             # brain for the AI caller, the judge, and Panchito
  provider: claude-cli           # claude-cli | anthropic | ollama
  model: haiku                   # claude-cli: haiku|sonnet|opus ; anthropic: model id ; ollama: model name
  budget_usd: 10                 # hard stop for the anthropic provider (estimated)
  max_calls_per_run: 60          # hard stop for any provider
tts:
  provider: kokoro               # kokoro (OpenAI compatible HTTP server)
  url: http://localhost:8880
stt:
  provider: faster-whisper
  model: small                   # tiny | base | small | medium
agents:
  - id: acme-demo
    name: ACME Appointments (demo)
    adapter: websocket           # websocket | http-turn (see docs/adapters.md)
    url: ws://localhost:8710/ws
    language: en
gate:
  pass_rate: 0.95
  critical_must_pass_all: true
```

## 2. Scenario file (`scenarios/*.yaml`, one scenario per file)

```yaml
id: book-happy-path              # unique, kebab-case, equals file name without .yaml
title: Customer books an appointment, happy path
agent: acme-demo                 # agent id from panchovoice.yaml
language: en                     # en | es
risk: critical                   # critical | normal
tags: [booking, happy-path]
fixture: acme-clinic.json        # file in fixtures_dir; seeds the mock tools and the caller facts
caller:
  mode: scripted                 # scripted | ai
  voice: af_heart                # Kokoro voice id
  speed: 1.0
  # scripted mode: a list of turns. Each turn waits for the agent to finish speaking, then says the text.
  turns:
    - say: "Hi, I need an appointment on Tuesday at 10 AM."
    - say: "My name is Sam Rivera."
    - say: "Yes, that is correct."
      # optional barge-in: start speaking while the agent is still talking
      # interrupt_after_ms: 1200    # ms after the agent starts its reply
    - say: "Thank you, goodbye."
  # ai mode: an LLM plays the caller
  persona: "Sam Rivera, a busy customer of ACME. Short answers. Gets impatient with long replies."
  goal: "Book an appointment for Tuesday at 10:00, then say goodbye."
  facts: { name: "Sam Rivera", phone: "555-0142" }
  opening_line: "Hi, I need an appointment."
  traits: { interrupts: false, patience_s: 6 }
audio:                           # optional degradations applied to caller audio
  noise: none                    # none | cafe | street | white
  snr_db: 20
expect:
  tool_calls:                    # every listed call must happen; args is a subset match
    - name: find_slot
      args: { day: tuesday, time: "10:00" }
    - name: book_appointment
      args: { name: "Sam Rivera", day: tuesday, time: "10:00" }
  forbidden_tools: [cancel_appointment]
  no_extra_tools: false          # true: any tool call not listed fails
  order: [find_slot, book_appointment]   # optional, relative order
  final_state:                   # subset match against the mock state after the call
    appointments:
      - { name: "Sam Rivera", day: tuesday, time: "10:00" }
  latency_ms: { p50: 1500, p95: 2500 }   # voice to voice, from the recording
  barge_in: { max_stop_ms: 2000 }        # only checked when a turn has interrupt_after_ms
  wer_max: 0.15                           # agent STT accuracy on what the caller said (needs transcript from agent)
  must_say: []                            # case-insensitive substrings the agent must say at least once
  never_say: ["system prompt"]            # case-insensitive substrings the agent must never say
  judge:                                  # each is a yes/no question for the LLM judge, answered with a quote
    - "The agent confirmed the day and the time with the caller before booking."
    - "The agent stayed polite."
limits: { max_turns: 12, max_seconds: 180, silence_timeout_s: 15 }
runs: 1                                    # how many times to repeat this scenario per suite run
```

Validation: pydantic v2 models in `panchovoice.scenario`. Unknown keys are an error. `scripted` needs `turns`; `ai` needs `persona` and `goal`.

## 3. Fixture file (`fixtures/*.json`)

The fixture is the one source of truth for the mock backend AND for caller facts.

```json
{
  "id": "acme-clinic",
  "description": "ACME clinic with open slots and two customers",
  "tools": [
    {"name": "find_slot", "description": "Check if a slot is free", "params": {"day": "string", "time": "string"},
     "action": "find", "collection": "slots", "match": ["day", "time"], "returns": "first_or_error"},
    {"name": "book_appointment", "description": "Book a slot for a customer", "params": {"name": "string", "day": "string", "time": "string"},
     "action": "insert", "collection": "appointments", "requires": {"collection": "slots", "match": ["day", "time"]}},
    {"name": "check_balance", "description": "Get a customer balance", "params": {"name": "string"},
     "action": "find", "collection": "customers", "match": ["name"], "returns": "first_or_error", "fields": ["name", "balance"]},
    {"name": "cancel_appointment", "description": "Cancel an appointment", "params": {"name": "string", "day": "string"},
     "action": "delete", "collection": "appointments", "match": ["name", "day"]}
  ],
  "state": {
    "slots": [{"day": "tuesday", "time": "10:00"}, {"day": "tuesday", "time": "11:00"}, {"day": "wednesday", "time": "09:00"}],
    "customers": [{"name": "Sam Rivera", "phone": "555-0142", "balance": 120.5}, {"name": "Jordan Lee", "phone": "555-0199", "balance": 0}],
    "appointments": []
  }
}
```

Mock tool semantics (implemented in core, `panchovoice.mocktools`):
- Matching is case-insensitive and trims spaces for strings. `"10"`, `"10:00"`, `"10 am"` normalize to `"10:00"` for any param named `time`. Days normalize to lowercase English weekday.
- `find`: returns `{"ok": true, "result": <row or selected fields>}` or `{"ok": false, "error": "not_found"}`.
- `insert`: if `requires` is set and no row matches, returns `{"ok": false, "error": "slot_not_available"}`; else appends params as a row, removes the matched slot row from `slots` (when collection is appointments and requires is slots), returns `{"ok": true}`.
- `delete`: removes matching rows, returns `{"ok": true, "deleted": n}`.
- Every call is recorded: `{t_ms, name, args, response}`.

## 4. Mock tool HTTP API (served by the core API server)

The agent under test calls tools through this API, so every call is recorded with exact args.

- `GET  /mock/{session}/tools` returns `[{name, description, params}]` (the agent may use this to build its tool list).
- `POST /mock/{session}/tools/{name}` body: JSON args. Returns the tool response JSON (always HTTP 200; errors are in the body).
- `GET  /mock/{session}/state` returns current state.
- `GET  /mock/{session}/calls` returns the recorded calls.

A session is created by the runner before each call. The session id is sent to the agent in the WebSocket `start` message (`mock_tools_url` = `http://localhost:8700/mock/{session}`).

## 5. WebSocket protocol `pv-ws/1` (caller <-> agent under test)

One WebSocket per call. The caller (PanchoVoiceAI) is the client. The agent is the server at `ws://host:8710/ws`.

Frames:
- Binary frames: raw audio, PCM16 LE mono 16 kHz, any length (the caller sends 20 ms frames = 640 bytes, paced in real time; silence is sent as zero frames so the stream is continuous).
- Text frames: JSON control messages.

Caller to agent (JSON):
- `{"type":"start","protocol":"pv-ws/1","session":"<id>","language":"en","mock_tools_url":"http://localhost:8700/mock/<id>","sample_rate":16000}`
- `{"type":"hangup"}`

Agent to caller (JSON):
- `{"type":"ready"}` after start.
- `{"type":"transcript","role":"user","text":"...","final":true}` what the agent heard (used for WER).
- `{"type":"transcript","role":"agent","text":"..."}` what the agent is about to say.
- `{"type":"tool_call","name":"...","args":{...}}` optional, for display only (the mock API is the source of truth).
- `{"type":"clear"}` the agent stopped speaking early (barge-in). The caller drops buffered agent audio.
- `{"type":"end","reason":"..."}` the agent ends the call.

Agent audio is sent back as binary frames, PCM16 LE mono 16 kHz, at real time pace or faster (the caller buffers and plays it out at real time on its timeline).

## 6. Run result (`data/runs/<run_id>/result.json`)

```json
{
  "run_id": "r_20261118_031500_ab12",
  "suite_id": "s_20261118_031500",
  "scenario_id": "book-happy-path",
  "agent_id": "acme-demo",
  "status": "passed",                 
  "started_at": "2026-11-18T03:15:00Z",
  "duration_ms": 41200,
  "turns": [
    {"index": 0, "caller_text": "Hi, I need ...", "caller_start_ms": 1000, "caller_end_ms": 3100,
     "agent_heard": "hi i need ...", "agent_text": "Sure ...", "agent_start_ms": 4020, "agent_end_ms": 7800,
     "latency_ms": 920, "interrupted": false, "stop_ms": null}
  ],
  "tool_calls": [{"t_ms": 3900, "name": "find_slot", "args": {"day": "tuesday", "time": "10:00"}, "response": {"ok": true}}],
  "final_state": {"appointments": []},
  "metrics": {
    "latency_p50_ms": 950, "latency_p95_ms": 1400, "latency_max_ms": 1500,
    "wer": 0.04, "barge_in_stop_ms": null, "turn_count": 4
  },
  "checks": [
    {"id": "tool_calls", "kind": "tools", "passed": true, "detail": "2 of 2 expected calls"},
    {"id": "judge_1", "kind": "judge", "passed": false, "detail": "The agent booked without confirming.", "evidence": "Agent: Done, you are booked."}
  ],
  "cost": {"llm_calls": 6, "usd_estimate": 0.0},
  "artifacts": {"stereo_wav": "stereo.wav", "caller_wav": "caller.wav", "agent_wav": "agent.wav", "transcript": "transcript.json"},
  "error": null
}
```

`status` is one of `passed | failed | error`. `error` means the harness could not run the test (agent down, timeout before first turn). It is never counted as a failure of the agent; it is excluded from the pass rate and shown apart.

Stereo WAV: left channel = caller, right channel = agent, 16 kHz, on the same timeline from `t0`.

Latency per turn = `agent_start_ms - caller_end_ms`, where `caller_end_ms` is the exact end sample of the caller TTS audio (known, not detected) and `agent_start_ms` is the first speech onset in the agent channel after `caller_end_ms`, found with Silero VAD (the onnx VAD in faster-whisper) on the recording. Percentiles: nearest rank over all turns of the run.

## 7. REST API (core server, `http://localhost:8700`, JSON, prefix `/api`)

CORS allows `http://localhost:3700`. The built panel (`web/out`) is served at `/` by the same server.

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/health` | | `{ok, version, checks: {tts, stt, llm, agents: {id: bool}}}` |
| GET | `/api/config` | | the parsed `panchovoice.yaml` (secrets removed) |
| GET | `/api/agents` | | `[{id, name, adapter, url, language, reachable}]` |
| GET | `/api/scenarios` | | `[{id, title, agent, risk, tags, language, mode, file}]` |
| GET | `/api/scenarios/{id}` | | `{id, yaml, parsed}` |
| PUT | `/api/scenarios/{id}` | `{yaml}` | validates, writes file, returns `{ok, errors}` |
| POST | `/api/scenarios/validate` | `{yaml}` | `{ok, errors:[{path, message}]}` |
| DELETE | `/api/scenarios/{id}` | | `{ok}` |
| GET | `/api/fixtures` / `/api/fixtures/{id}` | | list / `{id, json}` |
| PUT | `/api/fixtures/{id}` | `{json}` | `{ok, errors}` |
| POST | `/api/suites` | `{scenario_ids?: [], tags?: [], agent_id?: ""}` | starts a suite in the background, returns `{suite_id}` |
| GET | `/api/suites` | | `[{suite_id, started_at, status, total, passed, failed, errors, pass_rate, gate_passed}]` |
| GET | `/api/suites/{id}` | | suite summary + `runs: [run summary]` |
| GET | `/api/runs` | `?scenario_id=&limit=` | `[{run_id, suite_id, scenario_id, status, started_at, latency_p50_ms, latency_p95_ms, wer}]` |
| GET | `/api/runs/{id}` | | the full run result (section 6) |
| GET | `/api/runs/{id}/audio/{name}` | name: stereo, caller, agent | `audio/wav` |
| GET | `/api/stats` | | `{suites, runs, pass_rate_7d, latency_p50_7d, latency_p95_7d, last_suite}` |
| GET | `/api/onboarding` | | `{steps:[{id, title, done}]}` steps: `demo_running, first_run, viewed_report, agent_added, scenario_created, ci_enabled` |
| POST | `/api/onboarding/{step}` | | marks a step done |
| POST | `/api/panchito/chat` | `{messages:[{role, content}], context?: {page, run_id?, scenario_id?}}` | `{reply, proposals:[{kind: scenario|fixture, id, content, summary}]}` |

Panchito proposals are never saved by the server. The panel shows them and saves them through PUT only after the user clicks "Save".

## 8. CLI (`panchovoice`, alias `pv`)

- `pv init` interactive setup wizard (writes `panchovoice.yaml`, checks prerequisites, can start the demo, runs a smoke test, opens the panel).
- `pv doctor` checks prerequisites and prints fixes.
- `pv validate [paths]` validates scenario and fixture files.
- `pv run [--scenario ID ...] [--tag T] [--agent A] [--runs N] [--junit out.xml] [--json out.json] [--gate]` runs a suite; with `--gate` the exit code is 1 when the gate fails.
- `pv serve [--port 8700] [--open]` API server plus the built panel.
- `pv demo` starts the demo agent (local Python) on 8710.

Gate rule: `pass_rate = passed / (passed + failed)` over all runs (errors excluded). Gate passes when `pass_rate >= gate.pass_rate` AND (when `critical_must_pass_all`) every run of every critical scenario passed AND errors are 0.
