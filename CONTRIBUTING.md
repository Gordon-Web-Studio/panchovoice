# Contributing to PanchoVoiceAI

Thank you for your help. This page shows how to set up each part, how to test, and the style rules.

By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). Contributions are licensed under the [Apache License 2.0](LICENSE).

## The parts

| Part | Folder | Stack |
|---|---|---|
| Core engine, CLI, API server | `core/` | Python 3.12, package `panchovoice`, uv |
| Admin panel | `web/` | Next.js (App Router, TypeScript, Tailwind), static export |
| Demo agent | `demo/acme-agent/` | Python 3.12, package `acme_agent`, uv |
| Demo suite | `scenarios/`, `fixtures/` | YAML, JSON |
| Docs | `docs/`, `README.md` | Markdown |

The parts agree through one file: [docs/architecture/contract.md](docs/architecture/contract.md). It defines the config file, the scenario and fixture formats, the `pv-ws/1` protocol, the run result, the REST API and the CLI.

**If your change touches the contract, change the contract first** and add a row to [docs/architecture/decisions.md](docs/architecture/decisions.md) with the date and the reason.

## Dev setup

### Core

```bash
cd core
uv sync
uv run pv --help
uv run pytest
```

The unit tests need no services. They use a fake agent, a fake voice and a fake brain.

Tests marked `live` need local services (Kokoro, the `claude` CLI, models). They are skipped unless you set `PV_LIVE=1`:

```bash
PV_LIVE=1 uv run pytest
```

Check the demo suite files:

```bash
uv run pv validate
```

### Web

```bash
cd web
npm install
npm run dev          # http://localhost:3700
npm run lint
npm run build        # writes web/out/
```

The panel talks to the core API at `http://localhost:8700`. When the API is not up, the panel uses sample data and shows a "Demo data" badge, so you can work on the UI alone. Add `?mock=1` to the URL to force sample data.

### Demo agent

```bash
cd demo/acme-agent
uv run pytest -q                      # unit tests, no services needed
uv run acme-agent                     # needs Kokoro on 8880 and Ollama on 11434
uv run python tests/live_call.py      # a live self test against the running agent
```

### A full local check

With Kokoro, Ollama and the demo agent running:

```bash
cd core
uv run pv run --scenario book-happy-path
```

## Style rules

### Writing (code, comments, docs, commit messages)

- **No em dash and no en dash characters, anywhere.** Use a comma, a colon, parentheses, or two sentences. Check before you commit:

  ```bash
  grep -rlP "[\x{2013}\x{2014}]" README.md docs LICENSE CONTRIBUTING.md .github core/src web/app web/components web/lib demo scenarios fixtures
  ```

  The command must print nothing.
- **No real client or company names in examples**, tests, fixtures, UI text or docs. Use made up names: ACME, Sam Rivera, Jordan Lee. The only real names allowed are the tools and platforms we compare with or connect to.
- **No secrets and no internal data.** No keys, tokens, real phone numbers or real customer data. Example phone numbers use the 555 range.
- **No real customer audio** in the repo, in issues or in pull requests.
- Plain English. Short sentences. Active voice. Explain a term the first time you use it.
- Docs describe what exists. If something is not built, write "planned".

### Code

- Python: type hints, pydantic v2 models for file formats, unknown keys are errors.
- TypeScript: types for API shapes live in `web/lib/types.ts` and follow the contract.
- Audio inside the engine is PCM signed 16 bit little endian, mono, 16000 Hz.
- All times in results are integer milliseconds from the start of the call.
- A harness problem is an `error`, never a `failed`. Keep that line clear in every new check.
- Facts first: prefer a check on recorded tool calls, final state or the recording over a new judge prompt.

### Scenarios and fixtures

- The scenario `id` equals the file name. Fixture `id` too.
- Keep caller facts and fixture data in agreement.
- Run `uv run pv validate` before you commit.

## Pull requests

1. Open an issue first for a large change, so we can agree on the direction.
2. Keep a pull request to one purpose.
3. Add or update tests. A bug fix comes with a test that fails without the fix.
4. Update the docs when behavior changes.
5. Say in the description what you tested and how.

## Good first contributions

- A new demo scenario with a clear purpose.
- A doc fix: something that was unclear when you installed.
- A test for an edge case in the mock tools or the metrics.

Adapters for new platforms are welcome too. Please open an issue first: the adapter interface (transport, evidence, config) is still being shaped. See [docs/adapters.md](docs/adapters.md).

## Report a security problem

Do not open a public issue. See [SECURITY.md](SECURITY.md).
