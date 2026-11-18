// In-browser mock of the REST API in contract section 7.
import { parse } from "yaml";
import type {
  Agent,
  ChatContext,
  ChatMessage,
  ChatReply,
  Config,
  FixtureDetail,
  FixtureJson,
  FixtureSummary,
  Health,
  OnboardingStep,
  OnboardingStepId,
  RunResult,
  RunStatus,
  RunSummary,
  ScenarioDetail,
  ScenarioSummary,
  Stats,
  SuiteDetail,
  SuiteSummary,
  ValidationResult,
} from "../types";
import { MOCK_FIXTURES, MOCK_SCENARIOS } from "./scenarios";
import { buildRun, buildSuites, suiteDetail, suiteFromRuns, summarize, type MockRunSpec, type MockSuite } from "./runs";
import { validateFixtureJson, validateScenarioYaml } from "./validate";
import { buildStereoWav } from "./wav";

const ONBOARDING: { id: OnboardingStepId; title: string }[] = [
  { id: "demo_running", title: "Start the demo agent" },
  { id: "first_run", title: "Run your first suite" },
  { id: "viewed_report", title: "Open a run report" },
  { id: "agent_added", title: "Connect your own agent" },
  { id: "scenario_created", title: "Create a scenario" },
  { id: "ci_enabled", title: "Turn on the CI gate" },
];

const STORE_KEY = "pv-mock-v1";

interface Persisted {
  onboarding: Partial<Record<OnboardingStepId, boolean>>;
  scenarios: Record<string, string>;
  fixtures: Record<string, unknown>;
}

function load(): Persisted {
  const empty: Persisted = { onboarding: { demo_running: true, first_run: true }, scenarios: {}, fixtures: {} };
  if (typeof window === "undefined") return empty;
  try {
    const raw = window.localStorage.getItem(STORE_KEY);
    return raw ? { ...empty, ...JSON.parse(raw) } : empty;
  } catch {
    return empty;
  }
}

function delay<T>(value: T, ms = 160): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(structuredClone(value)), ms));
}

const RUNNING_MS = 7000;

class MockBackend {
  private state: Persisted = load();
  private suites: MockSuite[] = buildSuites(new Date());
  private running = new Map<string, { started: number; runs: MockRunSpec[] }>();
  private audioCache = new Map<string, string>();

  private save() {
    try {
      window.localStorage.setItem(STORE_KEY, JSON.stringify(this.state));
    } catch {
      /* storage blocked: keep state in memory only */
    }
  }

  private scenarios(): Record<string, string> {
    return { ...MOCK_SCENARIOS, ...this.state.scenarios };
  }

  private fixtures(): Record<string, unknown> {
    return { ...MOCK_FIXTURES, ...this.state.fixtures };
  }

  private tick() {
    for (const [id, job] of this.running) {
      if (Date.now() - job.started >= RUNNING_MS) {
        this.running.delete(id);
        const startedAt = new Date(job.started).toISOString().replace(/\.\d{3}Z$/, "Z");
        this.suites.unshift({ summary: suiteFromRuns(id, startedAt, job.runs), runs: job.runs });
        this.state.onboarding.first_run = true;
        this.save();
      }
    }
  }

  private allRuns(): MockRunSpec[] {
    this.tick();
    return this.suites.flatMap((s) => s.runs);
  }

  health(): Promise<Health> {
    return delay({ ok: true, version: "0.1.0-mock", checks: { tts: true, stt: true, llm: true, agents: { "acme-demo": true } } });
  }

  config(): Promise<Config> {
    return delay({
      version: 1,
      data_dir: "./data",
      scenarios_dir: "./scenarios",
      fixtures_dir: "./fixtures",
      llm: { provider: "claude-cli", model: "haiku", budget_usd: 10, max_calls_per_run: 60 },
      tts: { provider: "kokoro", url: "http://localhost:8880" },
      stt: { provider: "faster-whisper", model: "small" },
      agents: [
        { id: "acme-demo", name: "ACME Appointments (demo)", adapter: "websocket", url: "ws://localhost:8710/ws", language: "en" },
      ],
      gate: { pass_rate: 0.95, critical_must_pass_all: true },
    });
  }

  agents(): Promise<Agent[]> {
    return delay([
      { id: "acme-demo", name: "ACME Appointments (demo)", adapter: "websocket", url: "ws://localhost:8710/ws", language: "en", reachable: true },
      { id: "acme-staging", name: "ACME Appointments (staging)", adapter: "websocket", url: "ws://staging.acme.test:8710/ws", language: "en", reachable: false },
    ]);
  }

  listScenarios(): Promise<ScenarioSummary[]> {
    const list: ScenarioSummary[] = [];
    for (const [id, text] of Object.entries(this.scenarios())) {
      try {
        const p = parse(text) as Record<string, unknown> & { caller?: { mode?: string } };
        list.push({
          id,
          title: String(p.title ?? id),
          agent: String(p.agent ?? ""),
          risk: String(p.risk ?? "normal"),
          tags: Array.isArray(p.tags) ? p.tags.map(String) : [],
          language: String(p.language ?? "en"),
          mode: String(p.caller?.mode ?? "scripted"),
          file: `scenarios/${id}.yaml`,
        });
      } catch {
        list.push({ id, title: id, agent: "", risk: "normal", tags: [], language: "en", mode: "scripted", file: `scenarios/${id}.yaml` });
      }
    }
    return delay(list.sort((a, b) => a.id.localeCompare(b.id)));
  }

  getScenario(id: string): Promise<ScenarioDetail> {
    const text = this.scenarios()[id];
    if (!text) return Promise.reject(new Error(`Scenario ${id} not found`));
    let parsed: unknown = null;
    try {
      parsed = parse(text);
    } catch {
      parsed = null;
    }
    return delay({ id, yaml: text, parsed });
  }

  validateScenario(yaml: string): Promise<ValidationResult> {
    const errors = validateScenarioYaml(yaml);
    return delay({ ok: errors.length === 0, errors }, 60);
  }

  saveScenario(id: string, yaml: string): Promise<ValidationResult> {
    const errors = validateScenarioYaml(yaml);
    if (!errors.length) {
      this.state.scenarios[id] = yaml;
      if (!MOCK_SCENARIOS[id]) this.state.onboarding.scenario_created = true;
      this.save();
    }
    return delay({ ok: errors.length === 0, errors });
  }

  deleteScenario(id: string): Promise<{ ok: boolean }> {
    delete this.state.scenarios[id];
    this.save();
    return delay({ ok: true });
  }

  listFixtures(): Promise<FixtureSummary[]> {
    return delay(
      Object.entries(this.fixtures()).map(([id, json]) => ({
        id,
        description: String((json as FixtureJson).description ?? ""),
        file: `fixtures/${id}.json`,
      })),
    );
  }

  getFixture(id: string): Promise<FixtureDetail> {
    const json = this.fixtures()[id];
    if (!json) return Promise.reject(new Error(`Fixture ${id} not found`));
    return delay({ id, json: json as FixtureJson });
  }

  saveFixture(id: string, json: unknown): Promise<ValidationResult> {
    const errors = validateFixtureJson(json);
    if (!errors.length) {
      this.state.fixtures[id] = json;
      this.save();
    }
    return delay({ ok: errors.length === 0, errors });
  }

  startSuite(body: { scenario_ids?: string[]; tags?: string[]; agent_id?: string }): Promise<{ suite_id: string }> {
    const now = new Date();
    const p = (n: number) => String(n).padStart(2, "0");
    const stamp = `${now.getUTCFullYear()}${p(now.getUTCMonth() + 1)}${p(now.getUTCDate())}_${p(now.getUTCHours())}${p(now.getUTCMinutes())}${p(now.getUTCSeconds())}`;
    const suiteId = `s_${stamp}`;
    const scenarioIds = body.scenario_ids?.length ? body.scenario_ids : Object.keys(MOCK_SCENARIOS);
    const runs: MockRunSpec[] = scenarioIds.map((sid, i) => {
      const status: RunStatus = sid === "book-happy-path" && i === 0 ? "failed" : sid === "noisy-cafe-booking" ? "error" : "passed";
      return {
        run_id: `r_${stamp}_${(1000 + i).toString(16)}`,
        suite_id: suiteId,
        scenario_id: sid,
        status,
        started_at: new Date(now.getTime() + i * 600).toISOString().replace(/\.\d{3}Z$/, "Z"),
      };
    });
    this.running.set(suiteId, { started: now.getTime(), runs });
    return delay({ suite_id: suiteId });
  }

  listSuites(): Promise<SuiteSummary[]> {
    this.tick();
    const running: SuiteSummary[] = [...this.running.entries()].map(([id, job]) => ({
      suite_id: id,
      started_at: new Date(job.started).toISOString().replace(/\.\d{3}Z$/, "Z"),
      status: "running",
      total: job.runs.length,
      passed: 0,
      failed: 0,
      errors: 0,
      pass_rate: null,
      gate_passed: null,
    }));
    return delay([...running, ...this.suites.map((s) => s.summary)]);
  }

  getSuite(id: string): Promise<SuiteDetail> {
    this.tick();
    const job = this.running.get(id);
    if (job) {
      const done = Math.floor(((Date.now() - job.started) / RUNNING_MS) * job.runs.length);
      const finished = job.runs.slice(0, done);
      const summary = suiteFromRuns(id, new Date(job.started).toISOString(), finished, "running");
      return delay({ ...summary, total: job.runs.length, runs: finished.map((r) => summarize(buildRun(r))) });
    }
    const suite = this.suites.find((s) => s.summary.suite_id === id);
    if (!suite) return Promise.reject(new Error(`Suite ${id} not found`));
    return delay(suiteDetail(suite));
  }

  listRuns(query: { scenario_id?: string; limit?: number } = {}): Promise<RunSummary[]> {
    let runs = this.allRuns();
    if (query.scenario_id) runs = runs.filter((r) => r.scenario_id === query.scenario_id);
    runs = [...runs].sort((a, b) => b.started_at.localeCompare(a.started_at));
    if (query.limit) runs = runs.slice(0, query.limit);
    return delay(runs.map((r) => summarize(buildRun(r))));
  }

  getRun(id: string): Promise<RunResult> {
    const spec = this.allRuns().find((r) => r.run_id === id);
    if (!spec) return Promise.reject(new Error(`Run ${id} not found`));
    return delay(buildRun(spec));
  }

  async audioUrl(id: string): Promise<string> {
    const cached = this.audioCache.get(id);
    if (cached) return cached;
    const run = await this.getRun(id);
    const url = URL.createObjectURL(buildStereoWav(run));
    this.audioCache.set(id, url);
    return url;
  }

  stats(): Promise<Stats> {
    const all = this.allRuns().map(buildRun);
    const judged = all.filter((r) => r.status !== "error");
    const lat = all.flatMap((r) => r.turns.map((t) => t.latency_ms ?? 0)).sort((a, b) => a - b);
    const pick = (p: number) => (lat.length ? lat[Math.max(0, Math.ceil(p * lat.length) - 1)] : null);
    return delay({
      suites: this.suites.length,
      runs: all.length,
      pass_rate_7d: judged.length ? judged.filter((r) => r.status === "passed").length / judged.length : null,
      latency_p50_7d: pick(0.5),
      latency_p95_7d: pick(0.95),
      last_suite: this.suites[0]?.summary ?? null,
    });
  }

  onboarding(): Promise<{ steps: OnboardingStep[] }> {
    return delay({ steps: ONBOARDING.map((s) => ({ ...s, done: Boolean(this.state.onboarding[s.id]) })) }, 80);
  }

  markOnboarding(step: OnboardingStepId): Promise<{ ok: boolean }> {
    this.state.onboarding[step] = true;
    this.save();
    return delay({ ok: true }, 40);
  }

  resetOnboarding() {
    this.state.onboarding = { demo_running: true, first_run: true };
    this.save();
  }

  async chat(messages: ChatMessage[], context?: ChatContext): Promise<ChatReply> {
    const last = (messages.at(-1)?.content ?? "").toLowerCase();
    await new Promise((r) => setTimeout(r, 700));
    if (/(why|fail|wrong|explain)/.test(last)) {
      const runId = context?.run_id ?? this.suites[0]?.runs.find((r) => r.status === "failed")?.run_id;
      if (runId) {
        const run = await this.getRun(runId).catch(() => null);
        const failed = run?.checks.filter((c) => !c.passed) ?? [];
        if (run && run.status === "error") {
          return {
            reply: `This run is an **error**, not a failure. The harness could not finish the call: ${run.error}\n\nCheck that the agent is up (\`pv doctor\`), then run it again. Errors never count against the pass rate.`,
            proposals: [],
          };
        }
        if (run && failed.length) {
          const f = failed[0];
          return {
            reply: `${context?.run_id ? "" : `The last suite failed because of run \`${run.run_id}\` (${run.scenario_id}). `}The run failed on **${f.id}**. ${f.detail}${f.evidence ? `\n\nThe judge quotes: "${f.evidence}"` : ""}\n\nThe tool call was right (book_appointment with Tuesday, 10:00), so the mock state is fine. The problem is the conversation: the agent should read back the day and the time and wait for a yes before it books.\n\nFix idea for the agent prompt: "Before you call book_appointment, repeat the day and time and ask the caller to confirm."\n\nI can also add a stricter scenario that catches this every time.`,
            proposals: [
              {
                kind: "scenario",
                id: "book-must-confirm",
                summary: "Caller stays silent after the agent offers a slot. The agent must confirm before booking.",
                content: `id: book-must-confirm
title: Agent must confirm the time before booking
agent: acme-demo
language: en
risk: critical
tags: [booking, confirmation]
fixture: acme-clinic.json
caller:
  mode: scripted
  voice: af_heart
  turns:
    - say: "Tuesday at 10 please. Sam Rivera."
    - say: "Yes."
expect:
  order: [find_slot, book_appointment]
  must_say: ["ten"]
  judge:
    - "Before booking, the agent said the day and the time back and the caller said yes."
`,
              },
            ],
          };
        }
        return { reply: "This run passed every check. Nothing failed here. Want me to look at the slowest turn instead?", proposals: [] };
      }
      return {
        reply: "Open a run that failed and ask me again. I read its checks, tool calls and transcript and tell you what went wrong.",
        proposals: [],
      };
    }
    if (/(customer|mock|fixture|data)/.test(last)) {
      return {
        reply: "Here is a fixture with five customers, open slots for three days and one existing appointment. Review it, then save it. The caller facts and the mock tools both read from it.",
        proposals: [
          {
            kind: "fixture",
            id: "acme-five-customers",
            summary: "5 customers, 6 slots, 1 appointment, same tools as acme-clinic",
            content: JSON.stringify(
              {
                id: "acme-five-customers",
                description: "ACME clinic with five customers and a few open slots",
                tools: (MOCK_FIXTURES["acme-clinic"] as FixtureJson).tools,
                state: {
                  slots: [
                    { day: "monday", time: "09:00" },
                    { day: "monday", time: "13:30" },
                    { day: "tuesday", time: "10:00" },
                    { day: "tuesday", time: "16:00" },
                    { day: "thursday", time: "11:00" },
                    { day: "thursday", time: "15:00" },
                  ],
                  customers: [
                    { name: "Sam Rivera", phone: "555-0142", balance: 120.5 },
                    { name: "Jordan Lee", phone: "555-0199", balance: 0 },
                    { name: "Alex Kim", phone: "555-0107", balance: 35 },
                    { name: "Riley Park", phone: "555-0163", balance: 0 },
                    { name: "Casey Morgan", phone: "555-0128", balance: 210 },
                  ],
                  appointments: [{ name: "Riley Park", day: "monday", time: "11:00" }],
                },
              },
              null,
              2,
            ),
          },
        ],
      };
    }
    if (/(scenario|impatient|caller|test)/.test(last)) {
      return {
        reply: "Here is an AI caller who is short on time and interrupts long replies. It checks the booking and that the agent keeps its answers short.",
        proposals: [
          {
            kind: "scenario",
            id: "impatient-interrupter",
            summary: "AI caller, interrupts replies over 6 s, books Tuesday 10:00",
            content: `id: impatient-interrupter
title: Impatient caller who interrupts long replies
agent: acme-demo
language: en
risk: normal
tags: [booking, persona, barge-in]
fixture: acme-clinic.json
caller:
  mode: ai
  voice: am_adam
  persona: "Jordan Lee, in a hurry between meetings. Very short answers. Cuts in when a reply runs long."
  goal: "Book Tuesday at 10:00 and hang up fast."
  facts: { name: "Jordan Lee", phone: "555-0199" }
  opening_line: "Hi. I need Tuesday at ten. Fast, please."
  traits: { interrupts: true, patience_s: 4 }
expect:
  tool_calls:
    - name: book_appointment
      args: { name: "Jordan Lee", day: tuesday, time: "10:00" }
  barge_in: { max_stop_ms: 1500 }
  judge:
    - "The agent kept every reply short."
    - "The agent confirmed the time before booking."
limits: { max_turns: 10, max_seconds: 120, silence_timeout_s: 15 }
`,
          },
        ],
      };
    }
    return {
      reply:
        "I am Panchito. I can write scenarios, make mock data, and explain why a run failed. I only propose: nothing is saved until you click Save.\n\n(Demo data: these replies are canned. Start the core server for real answers.)",
      proposals: [],
    };
  }
}

let instance: MockBackend | null = null;
export function mockBackend(): MockBackend {
  if (!instance) instance = new MockBackend();
  return instance;
}
