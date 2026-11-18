# PanchoVoiceAI admin panel

The web panel of PanchoVoiceAI. Next.js (App Router, TypeScript, Tailwind CSS), built as a static export.
The core Python server (`pv serve`) serves the built files from `web/out` at `/`.

## Run it

```bash
cd web
npm install
npm run dev          # http://localhost:3700
```

The panel talks to the core API at `http://localhost:8700` (contract section 7).
When the API is not reachable, the panel switches to demo data on its own and shows a "Demo data" badge.

## Build

```bash
npm run build        # writes web/out/
npm run lint
```

`npm run build` runs `next build` and then `scripts/flatten-segments.mjs`. That step copies the
prefetch files that Next 16 writes in nested folders to the flat file names the browser asks for,
so any plain static server works with no 404s.

To look at the built panel without the core server:

```bash
npx serve out -l 3700
```

## Mock mode (demo data)

The panel uses local sample data from `lib/mock/` when one of these is true:

- the API does not answer `GET /api/health` within 1.5 s,
- the URL has `?mock=1` (kept for the browser tab; `?mock=0` turns it off),
- the build has `NEXT_PUBLIC_MOCK=1`.

The sample data has 6 suites of 12 runs, one failed run where the judge says the agent booked
without confirming the time, one barge-in run, one harness error, 10 scenarios and 2 fixtures.
The run recording is a small stereo WAV made in the browser (left caller, right agent), so the
player and waveform work. Saves in mock mode go to the browser's localStorage.

## Environment variables

| Name | Default | What it does |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8700` (same origin when the page is served from port 8700) | Base URL of the core API |
| `NEXT_PUBLIC_MOCK` | unset | `1` forces demo data |

Both are read at build time (`next build`).

## Pages

| Path | Screen |
|---|---|
| `/` | Home: last suite, gate bar, pass rate, P50 and P95, last runs, Run suite |
| `/runs/` | Runs with status filters; harness errors in their own list |
| `/runs/view/?id=` | Run detail: two channel player, turns, metrics, checks, tool calls, final state |
| `/suites/`, `/suites/view/?id=` | Suites and gate result |
| `/scenarios/`, `/scenarios/edit/?id=` | Scenario list with tag filter; YAML editor with live validation |
| `/fixtures/?id=` | Mock data: table view and JSON editor |
| `/agents/` | Agents and how to add one |
| `/settings/` | Read only config |
| `/onboarding/` | Getting started checklist (also a widget in the sidebar) |
| `/welcome/` | First visit welcome (Home shows it when there are no runs) |

Panchito, the AI companion, is a chat drawer on every page (button at the bottom right).
It sends the page context (`page`, `run_id`, `scenario_id`) to `POST /api/panchito/chat`.
Proposals are saved only when you click Save.

## Layout

- `app/` pages. Detail pages read the id from the query string, because a static export has no runtime routes.
- `components/` shell, Panchito drawer, waveform player, code editor, UI parts.
- `lib/api.ts` typed API client with the mock switch. `lib/types.ts` types from the contract.
- `lib/mock/` sample data, mock backend, WAV maker, client side scenario checks.
