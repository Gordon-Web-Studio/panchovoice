# Usage

All commands run from the `core/` folder with `uv run`. The CLI finds `panchovoice.yaml` in the current folder or in a parent folder.

## Commands

| Command | What it does |
|---|---|
| `pv init` | Setup wizard. Writes `panchovoice.yaml`. Options: `--yes`, `--answers FILE` |
| `pv doctor` | Checks prerequisites and prints fixes. Option: `--json` |
| `pv validate [paths]` | Validates scenario (`.yaml`) and fixture (`.json`) files |
| `pv run` | Runs a suite of scenarios and scores them |
| `pv serve` | Starts the API server and the built panel |
| `pv demo` | Starts the ACME demo agent on port 8710 |

Global options (before the command):

| Option | Meaning |
|---|---|
| `--config`, `-c` | Path to `panchovoice.yaml` (default: search the current folder and its parents) |
| `--version` | Show the version |
| `--help` | Show help |

You can also set the env variable `PV_CONFIG` to the path of the config file.

## Validate files

```bash
uv run pv validate                       # all files in scenarios_dir and fixtures_dir
uv run pv validate ../scenarios/book-happy-path.yaml
```

Each file gets `ok` or `error`. Errors show the path of the bad key, for example `caller.turns.0.say`. Unknown keys are errors, so a typo never passes in silence. The exit code is 1 when a file is invalid.

## Run a suite

A suite is one execution of one or more scenarios.

```bash
uv run pv run                                         # all scenarios
uv run pv run --scenario book-happy-path              # one scenario
uv run pv run -s book-happy-path -s noisy-line        # two scenarios
uv run pv run --tag booking                           # scenarios with a tag
uv run pv run --runs 3                                # repeat each scenario 3 times
uv run pv run --gate --junit out/junit.xml            # for CI
```

| Flag | Meaning |
|---|---|
| `--scenario`, `-s` | Scenario id. Repeat the flag for more |
| `--tag`, `-t` | Only scenarios with this tag. Repeat the flag: a scenario with any of the tags is selected |
| `--agent`, `-a` | Only scenarios written for this agent id. It filters; it does not point scenarios at another agent |
| `--runs`, `-n` | Repeat each scenario N times (default: the `runs` value of the scenario) |
| `--junit PATH` | Write JUnit XML to this file |
| `--json PATH` | Write the suite summary and all results as JSON to this file |
| `--gate` | Exit with code 1 when the gate fails |
| `--mock-port` | Port of the mock tool server (default 8700; a free port is used if it is busy) |

The agent under test must be running before you start. For the demo: `uv run pv demo` in another terminal.

While the suite runs, the terminal shows each caller line and each agent line. At the end you get a table:

| Column | Meaning |
|---|---|
| scenario | Scenario id |
| status | `passed`, `failed` or `error` |
| p50 ms, p95 ms | Voice to voice latency of the run |
| WER | Word error rate (0.00 is perfect), or `-` when not measured |
| barge-in ms | Stop time, or `-` when the scenario has no interruption |
| failed checks / error | The ids of the checks that did not pass |

The last line shows the counts, the pass rate, and the gate result.

## The three results

| Status | Meaning |
|---|---|
| `passed` | Every check passed |
| `failed` | At least one check failed. This is about your agent |
| `error` | The harness could not run or measure the test (agent down, judge down, no transcript for a WER check). This is about the setup, not about your agent |

Errors are not counted in the pass rate. They are shown apart. They do block the gate, because a test that did not run proves nothing.

## Where results live

Each run writes a folder in `data/runs/<run_id>/`:

| File | Content |
|---|---|
| `result.json` | The full result: turns, tool calls, final state, metrics, checks, cost |
| `stereo.wav` | Both sides on one timeline: left channel = caller, right channel = agent, 16 kHz |
| `caller.wav`, `agent.wav` | Each side alone |
| `transcript.json` | Lines with time, role and text, plus the raw agent messages |

`data/panchovoice.db` is a small SQLite index of suites and runs. The run folders stay the source of truth.

## Reading a result

Open `result.json`, or open the run in the panel. The important parts:

- `status`: the result of the run.
- `checks`: one entry per check, with `id`, `kind`, `status`, `detail`, and for judge and phrase checks an `evidence` quote.
- `turns`: per turn, what the caller said, what the agent heard, what the agent said, the times in ms, and `latency_ms`.
- `tool_calls`: each call with time, name, exact args and the response.
- `final_state`: the mock data after the call.
- `metrics`: `latency_p50_ms`, `latency_p95_ms`, `latency_max_ms`, `wer`, `barge_in_stop_ms`, `turn_count`.
- `ended_by`: who ended the call (`caller`, `agent`, `max_seconds`, `agent_closed`, `no_turns`).
- `notes`: harness notes, for example "no agent reply within 15000 ms".
- `cost`: number of LLM calls and the estimated USD.

A good order to read a failed run: first `checks` (what failed), then `tool_calls` and `final_state` (the facts), then listen to `stereo.wav`.

## The gate rule

```
pass_rate = passed / (passed + failed)      # errors are excluded
```

The gate passes when all of these are true:

1. `pass_rate` is at or above `gate.pass_rate` (default 0.95).
2. Every run of every `critical` scenario passed (when `gate.critical_must_pass_all` is true, the default).
3. There are no runs with `error`.

Without `--gate`, `pv run` still prints the gate result, but the exit code stays 0. More in [ci.md](ci.md).

## Exit codes

| Command | Code | Meaning |
|---|---|---|
| any | 2 | `panchovoice.yaml` is invalid |
| `pv validate` | 0 | All files are valid |
| `pv validate` | 1 | A file is invalid, or no files were found |
| `pv run` | 0 | The suite ran (also when runs failed, if `--gate` is not set) |
| `pv run --gate` | 1 | The gate failed |
| `pv run` | 2 | No scenario matches, or a scenario id is unknown or invalid |

## JUnit

`--junit out.xml` writes one `testcase` per run. A failed run has a `failure` element with the failed checks and their evidence. A harness error has an `error` element. The suite properties hold `pass_rate`, `gate_passed` and `gate_detail`. Most CI systems can show this file as a test report.

## The panel

Start it with:

```bash
uv run pv serve --open        # http://localhost:8700
```

`pv serve` serves the REST API and the built panel (`web/out`) on one port. Build the panel first (see [install.md](install.md)). Options: `--port` (default 8700) and `--open` (open the browser).

When the panel cannot reach the API, it shows sample data and a "Demo data" badge.

### Home

The last suite, the gate bar, the pass rate, P50 and P95, the last runs, and a "Run suite" button.

![Home](images/home.png)

### Run detail

The page you will use most. It has the two channel player (caller and agent waveforms on one timeline), the turns with their latency, the metrics, the checks with evidence quotes, the tool calls with exact arguments, and the final state.

![Run detail](images/run-detail.png)

### Scenarios and the editor

The scenario list has a tag filter. The editor is a YAML editor with live validation: errors show the path of the bad key while you type. Save writes the file in `scenarios_dir`.

![Scenario editor](images/scenario-editor.png)

### Onboarding

A getting started checklist with progress. It is also a small widget in the sidebar. The steps: run the demo, do a first run, view a report, add your agent, create a scenario, enable the CI gate. Two steps ("agent added" and "CI enabled") have an "I did this" button, because the panel cannot detect them.

![Onboarding](images/onboarding.png)

### Panchito

The AI companion is a chat drawer on every page (button at the bottom right). It knows which page, run or scenario you are looking at. Its proposals are saved only when you click Save. See [panchito.md](panchito.md).

![Panchito](images/panchito.png)

### Other pages

| Page | What it shows |
|---|---|
| Runs | All runs with status filters. Harness errors are in their own list |
| Suites | Suites and their gate result |
| Mock data (fixtures) | Table view and JSON editor |
| Agents | The configured agents and how to add one |
| Settings | The config, read only |
| Welcome | A first visit page (Home shows it when there are no runs) |
