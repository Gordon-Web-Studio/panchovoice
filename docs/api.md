# API

PanchoVoiceAI has two HTTP APIs, both served by the core server:

1. The **REST API** (`/api/...`): what the panel uses. You can use it from scripts and other apps.
2. The **mock tool API** (`/mock/...`): what the agent under test calls during a test.

Start the server:

```bash
cd core
uv run pv serve            # http://localhost:8700
```

All bodies are JSON. There is no login: the server is made for your own machine or a trusted network. Do not expose it to the internet. API tokens are planned.

CORS allows `http://localhost:3700` (the panel in dev mode). The built panel is served at `/` by the same server.

## REST API

Base URL: `http://localhost:8700`, prefix `/api`.

### Health and config

| Method | Path | Returns |
|---|---|---|
| GET | `/api/health` | `{ok, version, checks: {tts, stt, llm, agents: {id: bool}}}` |
| GET | `/api/config` | The parsed `panchovoice.yaml`, secrets removed |
| GET | `/api/agents` | `[{id, name, adapter, url, language, reachable}]` |
| GET | `/api/stats` | `{suites, runs, pass_rate_7d, latency_p50_7d, latency_p95_7d, last_suite}` |

### Scenarios

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/scenarios` | | `[{id, title, agent, risk, tags, language, mode, file}]` |
| GET | `/api/scenarios/{id}` | | `{id, yaml, parsed}` |
| PUT | `/api/scenarios/{id}` | `{yaml}` | Validates, writes the file, returns `{ok, errors}` |
| POST | `/api/scenarios/validate` | `{yaml}` | `{ok, errors: [{path, message}]}`. Writes nothing |
| DELETE | `/api/scenarios/{id}` | | `{ok}` |

### Fixtures

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/fixtures` | | The list of fixtures |
| GET | `/api/fixtures/{id}` | | `{id, json}` |
| PUT | `/api/fixtures/{id}` | `{json}` | `{ok, errors}` |

### Suites and runs

| Method | Path | Body or query | Returns |
|---|---|---|---|
| POST | `/api/suites` | `{scenario_ids?: [], tags?: [], agent_id?: ""}` | Starts a suite in the background. Returns `{suite_id}` |
| GET | `/api/suites` | | `[{suite_id, started_at, status, total, passed, failed, errors, pass_rate, gate_passed}]` |
| GET | `/api/suites/{id}` | | The suite summary plus `runs: [run summary]` |
| GET | `/api/runs` | `?scenario_id=&limit=` | `[{run_id, suite_id, scenario_id, status, started_at, latency_p50_ms, latency_p95_ms, wer}]` |
| GET | `/api/runs/{id}` | | The full run result |
| GET | `/api/runs/{id}/audio/{name}` | `name`: `stereo`, `caller` or `agent` | `audio/wav` |

### Onboarding and Panchito

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/onboarding` | | `{steps: [{id, title, done}]}` |
| POST | `/api/onboarding/{step}` | | Marks a step as done |
| POST | `/api/panchito/chat` | `{messages: [{role, content}], context?: {page, run_id?, scenario_id?}}` | `{reply, proposals: [{kind, id, content, summary}]}` |

Onboarding step ids: `demo_running`, `first_run`, `viewed_report`, `agent_added`, `scenario_created`, `ci_enabled`.

Panchito proposals (`kind` is `scenario` or `fixture`) are never saved by the server. A client saves one with the `PUT` endpoints after the user approves it. See [panchito.md](panchito.md).

## Examples

Check that everything is up:

```bash
curl http://localhost:8700/api/health
```

Validate a scenario without saving it:

```bash
curl -X POST http://localhost:8700/api/scenarios/validate \
  -H "Content-Type: application/json" \
  -d '{"yaml": "id: my-test\ntitle: My test\nagent: acme-demo\ncaller:\n  mode: scripted\n  turns:\n    - say: \"Hello.\"\n"}'
```

```json
{"ok": true, "errors": []}
```

A bad file gives the path of each problem:

```json
{"ok": false, "errors": [{"path": "caller.turnz", "message": "unknown key"}]}
```

Start a suite and follow it:

```bash
curl -X POST http://localhost:8700/api/suites \
  -H "Content-Type: application/json" \
  -d '{"scenario_ids": ["book-happy-path", "confirm-before-booking"]}'
# {"suite_id": "s_20261118_031500"}

curl http://localhost:8700/api/suites/s_20261118_031500
```

Poll `GET /api/suites/{id}` until the suite is done. Then read each run:

```bash
curl http://localhost:8700/api/runs/r_20261118_031500_ab12
curl -o call.wav http://localhost:8700/api/runs/r_20261118_031500_ab12/audio/stereo
```

### The run result

`GET /api/runs/{id}` returns the same JSON as `data/runs/<run_id>/result.json`:

```json
{
  "run_id": "r_20261118_031500_ab12",
  "suite_id": "s_20261118_031500",
  "scenario_id": "book-happy-path",
  "agent_id": "acme-demo",
  "status": "failed",
  "started_at": "2026-11-18T03:15:00Z",
  "duration_ms": 41200,
  "turns": [
    {"index": 0, "caller_text": "Hi, I need ...", "caller_start_ms": 1000, "caller_end_ms": 3100,
     "agent_heard": "hi i need ...", "agent_text": "Sure ...", "agent_start_ms": 4020, "agent_end_ms": 7800,
     "latency_ms": 920, "interrupted": false, "stop_ms": null}
  ],
  "tool_calls": [
    {"t_ms": 3900, "name": "find_slot", "args": {"day": "tuesday", "time": "10:00"}, "response": {"ok": true}}
  ],
  "final_state": {"appointments": []},
  "metrics": {
    "latency_p50_ms": 950, "latency_p95_ms": 1400, "latency_max_ms": 1500,
    "wer": 0.04, "barge_in_stop_ms": null, "turn_count": 4
  },
  "checks": [
    {"id": "tool_calls", "kind": "tools", "passed": true, "status": "passed", "detail": "2 of 2 expected calls"},
    {"id": "judge_1", "kind": "judge", "passed": false, "status": "failed",
     "detail": "The agent confirmed the day and the time with the caller before booking.",
     "evidence": "Agent: Done, you are booked."}
  ],
  "cost": {"llm_calls": 6, "usd_estimate": 0.0},
  "artifacts": {"stereo_wav": "stereo.wav", "caller_wav": "caller.wav", "agent_wav": "agent.wav", "transcript": "transcript.json"},
  "error": null
}
```

- `status` is `passed`, `failed` or `error`. `error` means the harness could not run or measure the test. It is never a failure of the agent.
- Each check has `status` (`passed`, `failed`, `error`) and `passed` (`true`, `false`, or `null` for an error).
- All times are integer milliseconds from the start of the call.
- The result also has `risk`, `title`, `language`, `mode`, `notes` and `ended_by`.

## Mock tool API

The agent under test calls its tools here, so every call is recorded with exact arguments.

The runner creates one **session** per call. The agent gets the base URL in the `start` message of the `pv-ws/1` protocol:

```
mock_tools_url = http://127.0.0.1:8700/mock/<session>
```

Always use the URL from the `start` message. The port is not always 8700: `pv run` serves this API itself and picks a free port when 8700 is busy.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/mock/{session}/tools` | | `[{name, description, params}]`. The agent can build its tool list from this |
| POST | `/mock/{session}/tools/{name}` | The JSON args | The tool response. Always HTTP 200; errors are in the body |
| GET | `/mock/{session}/state` | | The current mock data |
| GET | `/mock/{session}/calls` | | The recorded calls: `[{t_ms, name, args, response}]` |

An unknown session gives HTTP 404.

Example, as an agent would call it:

```bash
curl http://127.0.0.1:8700/mock/m_3f9a1c2b4d5e/tools
```

```json
[
  {"name": "find_slot", "description": "Check if a slot is free on a day and time", "params": {"day": "string", "time": "string"}},
  {"name": "book_appointment", "description": "Book a slot for a customer", "params": {"name": "string", "day": "string", "time": "string"}}
]
```

```bash
curl -X POST http://127.0.0.1:8700/mock/m_3f9a1c2b4d5e/tools/book_appointment \
  -H "Content-Type: application/json" \
  -d '{"name": "Sam Rivera", "day": "Tuesday", "time": "10 am"}'
```

```json
{"ok": true}
```

Responses:

| Case | Body |
|---|---|
| `find`, found | `{"ok": true, "result": {...}}` (a list when the tool returns `all`) |
| `find`, nothing | `{"ok": false, "error": "not_found"}` |
| `insert`, done | `{"ok": true}` |
| `insert`, the required row is missing | `{"ok": false, "error": "slot_not_available"}` |
| `delete` | `{"ok": true, "deleted": 1}` |
| A needed param is empty | `{"ok": false, "error": "missing_params", "params": ["time"]}` |
| The tool is not in the fixture | `{"ok": false, "error": "unknown_tool"}` |

The tools and the data come from the fixture of the scenario. See [scenarios.md](scenarios.md#fixture-reference).

## Connect a CRM or another web app

**What works today: the REST API.** Any app that can make HTTP requests can drive PanchoVoiceAI:

| You want to | Do this |
|---|---|
| Run tests from another system (a deploy script, an admin tool, a workflow tool) | `POST /api/suites`, then poll `GET /api/suites/{id}` |
| Show results in your own dashboard | `GET /api/stats`, `GET /api/suites`, `GET /api/runs` |
| Open a ticket or a CRM task for each failed run | Poll the suite. For each run with `status: failed`, read `GET /api/runs/{id}` and send the failed `checks` and their `evidence` to your system |
| Keep the recording with the ticket | `GET /api/runs/{id}/audio/stereo` |
| Create scenarios from your own tool | `POST /api/scenarios/validate`, then `PUT /api/scenarios/{id}` |

A small polling script is enough for most cases. The CLI is another path: `pv run --json out.json` writes the summary and all results to one file that a script can read.

**Planned, not built:**

- **Webhooks**: a call to your URL when a suite ends (for CRMs, chat tools and automation tools).
- **MCP server**: tools such as `list_scenarios`, `run_suite`, `get_run`, `explain_failure` and `create_scenario`, so a coding agent can run and read tests. Changes will ask for confirmation.
- **API tokens**.
