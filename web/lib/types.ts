// Types from docs/architecture/contract.md sections 1, 6 and 7.

export type RunStatus = "passed" | "failed" | "error";
export type SuiteStatus = "running" | "done" | "passed" | "failed" | "error" | string;

export interface Health {
  ok: boolean;
  version: string;
  checks: { tts: boolean; stt: boolean; llm: boolean; agents: Record<string, boolean> };
}

export interface AgentConfig {
  id: string;
  name: string;
  adapter: string;
  url: string;
  language: string;
}

export interface Config {
  version: number;
  data_dir?: string;
  scenarios_dir?: string;
  fixtures_dir?: string;
  llm?: { provider: string; model: string; budget_usd?: number; max_calls_per_run?: number };
  tts?: { provider: string; url?: string; model?: string; default_voice?: string };
  stt?: { provider: string; model?: string };
  agents?: AgentConfig[];
  gate?: { pass_rate: number; critical_must_pass_all: boolean };
  [key: string]: unknown;
}

export interface Agent extends AgentConfig {
  reachable: boolean;
}

export interface ScenarioSummary {
  id: string;
  title: string;
  agent: string;
  risk: "critical" | "normal" | string;
  tags: string[];
  language: string;
  mode: "scripted" | "ai" | string;
  file: string;
}

export interface ScenarioDetail {
  id: string;
  yaml: string;
  parsed: unknown;
}

export interface ValidationError {
  path: string;
  message: string;
}

export interface ValidationResult {
  ok: boolean;
  errors: ValidationError[];
}

export interface FixtureSummary {
  id: string;
  description?: string;
  file?: string;
}

export interface FixtureTool {
  name: string;
  description?: string;
  params?: Record<string, string>;
  action?: string;
  collection?: string;
  [key: string]: unknown;
}

export interface FixtureJson {
  id: string;
  description?: string;
  tools?: FixtureTool[];
  state?: Record<string, Record<string, unknown>[]>;
  [key: string]: unknown;
}

export interface FixtureDetail {
  id: string;
  json: FixtureJson;
}

export interface Turn {
  index: number;
  caller_text: string;
  caller_start_ms: number;
  caller_end_ms: number;
  agent_heard: string;
  agent_text: string;
  agent_start_ms: number;
  agent_end_ms: number;
  latency_ms: number | null;
  interrupted: boolean;
  stop_ms: number | null;
}

export interface ToolCall {
  t_ms: number;
  name: string;
  args: Record<string, unknown>;
  response: unknown;
}

export interface Check {
  id: string;
  kind: string;
  passed: boolean;
  detail: string;
  evidence?: string | null;
}

export interface RunMetrics {
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  latency_max_ms: number | null;
  wer: number | null;
  barge_in_stop_ms: number | null;
  turn_count: number;
}

export interface RunResult {
  run_id: string;
  suite_id: string;
  scenario_id: string;
  agent_id: string;
  status: RunStatus;
  started_at: string;
  duration_ms: number;
  turns: Turn[];
  tool_calls: ToolCall[];
  final_state: Record<string, unknown>;
  metrics: RunMetrics;
  checks: Check[];
  cost: { llm_calls: number; usd_estimate: number };
  artifacts: Record<string, string>;
  error: string | null;
}

export interface RunSummary {
  run_id: string;
  suite_id: string;
  scenario_id: string;
  status: RunStatus;
  started_at: string;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  wer: number | null;
}

export interface SuiteSummary {
  suite_id: string;
  started_at: string;
  status: SuiteStatus;
  total: number;
  passed: number;
  failed: number;
  errors: number;
  pass_rate: number | null;
  gate_passed: boolean | null;
}

export interface SuiteDetail extends SuiteSummary {
  runs: RunSummary[];
}

export interface Stats {
  suites: number;
  runs: number;
  pass_rate_7d: number | null;
  latency_p50_7d: number | null;
  latency_p95_7d: number | null;
  last_suite: SuiteSummary | null;
}

export type OnboardingStepId =
  | "demo_running"
  | "first_run"
  | "viewed_report"
  | "agent_added"
  | "scenario_created"
  | "ci_enabled";

export interface OnboardingStep {
  id: OnboardingStepId;
  title: string;
  done: boolean;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ChatContext {
  page: string;
  run_id?: string;
  scenario_id?: string;
}

export interface Proposal {
  kind: "scenario" | "fixture";
  id: string;
  content: string;
  summary: string;
}

export interface ChatReply {
  reply: string;
  proposals: Proposal[];
}

export type AudioName = "stereo" | "caller" | "agent";
