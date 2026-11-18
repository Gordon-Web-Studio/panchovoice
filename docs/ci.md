# CI gate

The gate turns a suite into one answer: go or no go. Use it to block a release when the voice agent got worse.

## The gate rule

```
pass_rate = passed / (passed + failed)
```

The rate is counted over **runs**, not over scenarios. Runs with harness errors are not in the rate.

The gate passes when all three are true:

| # | Rule | Setting |
|---|---|---|
| 1 | `pass_rate` is at or above the minimum | `gate.pass_rate`, default `0.95` (95 percent) |
| 2 | Every run of every `critical` scenario passed | `gate.critical_must_pass_all`, default `true` |
| 3 | No run has the status `error` | Always on |

Why these rules:

- **A pass rate, not "all green".** Voice agents use LLMs, and an LLM does not behave the same every time. One unlucky run in 40 should not block a release. Two should make you look.
- **Critical scenarios have no tolerance.** Money, privacy, security and the main flow must pass every run.
- **Errors block, but are not failures.** If the agent was down or the judge could not answer, the test proved nothing. The gate stops, and the report shows the errors apart, so nobody blames the agent for a broken setup.

Count example: 12 scenarios with `runs: 3` give 36 runs. One failed run gives 97 percent (pass). Two failed runs give 94 percent (blocked).

About the default: 95 percent is a project default, not an industry number. Other tools use 75 percent, or "all 3 runs must pass". Change `gate.pass_rate` to fit your risk.

## Run the gate

```bash
cd core
uv run pv run --gate --junit out/junit.xml --json out/suite.json
```

| Exit code | Meaning |
|---|---|
| 0 | The gate passed |
| 1 | The gate failed |
| 2 | Bad config, or no scenario matched |

The last line of the output names the reason, for example:
`gate FAILED (critical scenarios not passed: confirm-before-booking)`.

- `--junit` writes a JUnit XML file. Most CI systems show it as a test report. Failed checks and their evidence quotes are in the `failure` elements. Harness errors are `error` elements.
- `--json` writes the suite summary and every result, for scripts and coding agents.

## Three test levels

Audio tests are slow and use real compute. Do not run all of them on every commit.

| Level | When | What | Status |
|---|---|---|---|
| **Text** | Every pull request (seconds) | The same scenarios without audio: caller text in, agent text out | Planned |
| **Audio** | Every night, or when the prompt, the model or the voice changes | Full voice to voice calls, as described in these docs | Works today |
| **Phone** | Before a release | A real phone call through a telephony provider | Planned (needs the Twilio adapter) |

Until the text level exists, a small audio subset is a good pull request check: tag 2 or 3 fast scripted scenarios as `smoke` and run `pv run --tag smoke --gate`.

## Example GitHub Actions workflow

The repo has an example at `docs/examples/voice-tests.yml`. To use it, copy it to `.github/workflows/voice-tests.yml` in your repo.

**It is an example. A ready-made reusable GitHub Action is on the roadmap, not built.** The example was written from these docs and has not been proven on a GitHub runner yet. Expect to adjust it.

What it does:

1. Starts Kokoro as a service container.
2. Installs uv and Ollama and pulls a small model.
3. Writes a CI config file (`panchovoice.ci.yaml`) with the `ollama` brain.
4. Starts the agent under test. In the example this is the demo agent with the bug off. **Replace this step with your own agent.**
5. Runs `pv validate`, then `pv run --tag smoke --gate --junit ...`.
6. Uploads the JUnit file and the run folders (results and recordings) as an artifact.

Triggers: by hand (`workflow_dispatch`), and nightly at 03:00 UTC. The nightly run only starts when you set the repository variable `PV_NIGHTLY` to `true`. This guard is there because the job is heavy.

The core of the file:

```yaml
on:
  workflow_dispatch:
  schedule:
    - cron: "0 3 * * *"

jobs:
  voice-tests:
    if: github.event_name == 'workflow_dispatch' || vars.PV_NIGHTLY == 'true'
    runs-on: ubuntu-latest
    timeout-minutes: 60
    services:
      kokoro:
        image: ghcr.io/remsky/kokoro-fastapi-cpu:latest
        ports:
          - 8880:8880
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.12"
      # ... install Ollama, write the CI config, start your agent ...
      - name: Run the suite with the gate
        working-directory: core
        run: >
          uv run pv --config ../panchovoice.ci.yaml run
          --tag smoke --gate
          --junit ../out/junit.xml --json ../out/suite.json
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: voice-test-results
          path: |
            out/
            data/runs/
```

### The brain in CI

The default brain, `claude-cli`, needs a signed-in Claude Code. A CI runner does not have that. Choose one of these for CI:

| Option | Config | Notes |
|---|---|---|
| Local model | `llm.provider: ollama` | No secrets. Slower on a hosted runner. The judge quality depends on the model |
| Anthropic API | `llm.provider: anthropic` | Add `ANTHROPIC_API_KEY` as a repository secret and pass it as an env variable. `llm.budget_usd` is the hard stop |

Never put a key in a workflow file or in `panchovoice.yaml`.

### Speed on hosted runners

Hosted runners have no GPU. The demo agent will be slow there, and latency checks can fail for that reason alone. For your own agent this matters less when the agent runs somewhere else and the runner only places the calls. For serious latency numbers, use a self-hosted runner with stable hardware, and compare against a baseline from the same machine.

## When to trigger

- Nightly, for the full audio suite.
- When the prompt, the model or the voice of the agent changes. For agents that live in code, use a `paths` filter on those files.
- By hand before a release.

Planned: a config fingerprint for platforms where the agent setup lives behind an API (the suite runs when the fingerprint changes), a comment on the pull request, a baseline comparison (block when P95 rises 10 percent or WER rises 2 points), and webhooks.

## Recordings in CI

Run folders hold audio of the test calls. With the demo and scripted callers this is synthetic audio, so it is safe to upload. If your tests ever include real customer audio, do not upload it as a public artifact.
