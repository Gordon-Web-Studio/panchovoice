// Typed client for the core REST API (contract section 7), with a mock fallback.
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
  RunSummary,
  ScenarioDetail,
  ScenarioSummary,
  Stats,
  SuiteDetail,
  SuiteSummary,
  ValidationResult,
} from "./types";
import { mockBackend } from "./mock/backend";

export type Mode = "live" | "mock";

const MOCK_KEY = "pv-force-mock";

export function apiBase(): string {
  const env = process.env.NEXT_PUBLIC_API_URL;
  if (env) return env.replace(/\/$/, "");
  if (typeof window !== "undefined" && window.location.port === "8700") return "";
  return "http://localhost:8700";
}

function forcedMock(): boolean {
  if (process.env.NEXT_PUBLIC_MOCK === "1") return true;
  if (typeof window === "undefined") return false;
  try {
    const q = new URLSearchParams(window.location.search).get("mock");
    if (q === "1") window.sessionStorage.setItem(MOCK_KEY, "1");
    if (q === "0") window.sessionStorage.removeItem(MOCK_KEY);
    return window.sessionStorage.getItem(MOCK_KEY) === "1";
  } catch {
    return false;
  }
}

// ---- mode store (read by the "Demo data" badge) ----
let mode: Mode | null = null;
let modePromise: Promise<Mode> | null = null;
const listeners = new Set<() => void>();

export function subscribeMode(fn: () => void) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}
export function currentMode(): Mode | null {
  return mode;
}

function setMode(m: Mode) {
  mode = m;
  listeners.forEach((fn) => fn());
}

export function getMode(): Promise<Mode> {
  if (modePromise) return modePromise;
  modePromise = (async () => {
    if (forcedMock()) {
      setMode("mock");
      return "mock" as Mode;
    }
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 1500);
      const res = await fetch(`${apiBase()}/api/health`, { signal: ctrl.signal });
      clearTimeout(timer);
      if (!res.ok) throw new Error(String(res.status));
      setMode("live");
      return "live" as Mode;
    } catch {
      setMode("mock");
      return "mock" as Mode;
    }
  })();
  return modePromise;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

async function http<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  const res = await fetch(`${apiBase()}${path}`, {
    ...rest,
    headers: json !== undefined ? { "Content-Type": "application/json", ...(rest.headers ?? {}) } : rest.headers,
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* body is not JSON */
    }
    throw new ApiError(detail, res.status);
  }
  return (await res.json()) as T;
}

async function pick<T>(live: () => Promise<T>, mock: () => Promise<T>): Promise<T> {
  return (await getMode()) === "mock" ? mock() : live();
}

const m = () => mockBackend();
const enc = encodeURIComponent;

// The fixtures list shape is not fixed by the contract. Accept strings or objects.
function normalizeFixtures(raw: unknown): FixtureSummary[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((item) => (typeof item === "string" ? { id: item.replace(/\.json$/, "") } : (item as FixtureSummary)));
}

// `json` in GET /api/fixtures/{id} may be an object or a JSON string.
function normalizeFixture(raw: { id: string; json: unknown }): FixtureDetail {
  let json = raw.json;
  if (typeof json === "string") {
    try {
      json = JSON.parse(json);
    } catch {
      json = {};
    }
  }
  return { id: raw.id, json: json as FixtureJson };
}

export const api = {
  health: () => pick(() => http<Health>("/api/health"), () => m().health()),
  config: () => pick(() => http<Config>("/api/config"), () => m().config()),
  agents: () => pick(() => http<Agent[]>("/api/agents"), () => m().agents()),

  scenarios: () => pick(() => http<ScenarioSummary[]>("/api/scenarios"), () => m().listScenarios()),
  scenario: (id: string) => pick(() => http<ScenarioDetail>(`/api/scenarios/${enc(id)}`), () => m().getScenario(id)),
  saveScenario: (id: string, yaml: string) =>
    pick(
      () => http<ValidationResult>(`/api/scenarios/${enc(id)}`, { method: "PUT", json: { yaml } }),
      () => m().saveScenario(id, yaml),
    ),
  validateScenario: (yaml: string) =>
    pick(
      () => http<ValidationResult>("/api/scenarios/validate", { method: "POST", json: { yaml } }),
      () => m().validateScenario(yaml),
    ),
  deleteScenario: (id: string) =>
    pick(() => http<{ ok: boolean }>(`/api/scenarios/${enc(id)}`, { method: "DELETE" }), () => m().deleteScenario(id)),

  fixtures: () => pick(async () => normalizeFixtures(await http<unknown>("/api/fixtures")), () => m().listFixtures()),
  fixture: (id: string) =>
    pick(async () => normalizeFixture(await http<{ id: string; json: unknown }>(`/api/fixtures/${enc(id)}`)), () => m().getFixture(id)),
  saveFixture: (id: string, json: unknown) =>
    pick(
      () => http<ValidationResult>(`/api/fixtures/${enc(id)}`, { method: "PUT", json: { json } }),
      () => m().saveFixture(id, json),
    ),

  startSuite: (body: { scenario_ids?: string[]; tags?: string[]; agent_id?: string } = {}) =>
    pick(() => http<{ suite_id: string }>("/api/suites", { method: "POST", json: body }), () => m().startSuite(body)),
  suites: () => pick(() => http<SuiteSummary[]>("/api/suites"), () => m().listSuites()),
  suite: (id: string) => pick(() => http<SuiteDetail>(`/api/suites/${enc(id)}`), () => m().getSuite(id)),

  runs: (query: { scenario_id?: string; limit?: number } = {}) => {
    const qs = new URLSearchParams();
    if (query.scenario_id) qs.set("scenario_id", query.scenario_id);
    if (query.limit) qs.set("limit", String(query.limit));
    const suffix = qs.toString() ? `?${qs}` : "";
    return pick(() => http<RunSummary[]>(`/api/runs${suffix}`), () => m().listRuns(query));
  },
  run: (id: string) => pick(() => http<RunResult>(`/api/runs/${enc(id)}`), () => m().getRun(id)),
  audioUrl: (id: string, name: "stereo" | "caller" | "agent" = "stereo") =>
    pick(async () => `${apiBase()}/api/runs/${enc(id)}/audio/${name}`, () => m().audioUrl(id)),

  stats: () => pick(() => http<Stats>("/api/stats"), () => m().stats()),
  onboarding: () => pick(() => http<{ steps: OnboardingStep[] }>("/api/onboarding"), () => m().onboarding()),
  markOnboarding: (step: OnboardingStepId) =>
    pick(() => http<{ ok: boolean }>(`/api/onboarding/${step}`, { method: "POST" }), () => m().markOnboarding(step)),

  chat: (messages: ChatMessage[], context?: ChatContext) =>
    pick(
      () => http<ChatReply>("/api/panchito/chat", { method: "POST", json: { messages, context } }),
      () => m().chat(messages, context),
    ),
};
