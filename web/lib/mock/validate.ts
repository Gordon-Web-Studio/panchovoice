// Client side scenario checks for mock mode. The real rules live in core (pydantic).
import { parseDocument } from "yaml";
import type { ValidationError } from "../types";

const TOP_KEYS = ["id", "title", "agent", "language", "risk", "tags", "fixture", "caller", "audio", "expect", "limits", "runs"];
const CALLER_KEYS = ["mode", "voice", "speed", "turns", "persona", "goal", "facts", "opening_line", "traits"];
const EXPECT_KEYS = [
  "tool_calls",
  "forbidden_tools",
  "no_extra_tools",
  "order",
  "final_state",
  "latency_ms",
  "barge_in",
  "wer_max",
  "must_say",
  "never_say",
  "judge",
];

export function validateScenarioYaml(text: string): ValidationError[] {
  const errors: ValidationError[] = [];
  const doc = parseDocument(text);
  for (const e of doc.errors) {
    const line = e.linePos?.[0]?.line;
    errors.push({ path: line ? `line ${line}` : "(yaml)", message: e.message.split("\n")[0] });
  }
  if (errors.length) return errors;
  const data = doc.toJS() as Record<string, unknown> | null;
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return [{ path: "(root)", message: "The file must be a YAML mapping." }];
  }
  for (const key of Object.keys(data)) {
    if (!TOP_KEYS.includes(key)) errors.push({ path: key, message: `Unknown key "${key}".` });
  }
  for (const key of ["id", "title", "agent", "language", "risk", "caller", "expect"]) {
    if (data[key] === undefined) errors.push({ path: key, message: "Field required." });
  }
  if (typeof data.id === "string" && !/^[a-z0-9]+(-[a-z0-9]+)*$/.test(data.id)) {
    errors.push({ path: "id", message: "Use kebab-case: lowercase letters, numbers and hyphens." });
  }
  if (data.language !== undefined && !["en", "es"].includes(String(data.language))) {
    errors.push({ path: "language", message: "Must be en or es." });
  }
  if (data.risk !== undefined && !["critical", "normal"].includes(String(data.risk))) {
    errors.push({ path: "risk", message: "Must be critical or normal." });
  }
  if (data.tags !== undefined && !Array.isArray(data.tags)) {
    errors.push({ path: "tags", message: "Must be a list." });
  }
  const caller = data.caller as Record<string, unknown> | undefined;
  if (caller && typeof caller === "object") {
    for (const key of Object.keys(caller)) {
      if (!CALLER_KEYS.includes(key)) errors.push({ path: `caller.${key}`, message: `Unknown key "${key}".` });
    }
    const mode = caller.mode;
    if (mode !== "scripted" && mode !== "ai") {
      errors.push({ path: "caller.mode", message: "Must be scripted or ai." });
    } else if (mode === "scripted") {
      if (!Array.isArray(caller.turns) || caller.turns.length === 0) {
        errors.push({ path: "caller.turns", message: "Scripted mode needs at least one turn." });
      } else {
        caller.turns.forEach((turn, i) => {
          if (!turn || typeof turn !== "object" || typeof (turn as Record<string, unknown>).say !== "string") {
            errors.push({ path: `caller.turns.${i}.say`, message: "Each turn needs a say text." });
          }
        });
      }
    } else {
      if (!caller.persona) errors.push({ path: "caller.persona", message: "AI mode needs a persona." });
      if (!caller.goal) errors.push({ path: "caller.goal", message: "AI mode needs a goal." });
    }
  }
  const expect = data.expect as Record<string, unknown> | undefined;
  if (expect && typeof expect === "object") {
    for (const key of Object.keys(expect)) {
      if (!EXPECT_KEYS.includes(key)) errors.push({ path: `expect.${key}`, message: `Unknown key "${key}".` });
    }
    if (expect.tool_calls !== undefined) {
      if (!Array.isArray(expect.tool_calls)) {
        errors.push({ path: "expect.tool_calls", message: "Must be a list." });
      } else {
        expect.tool_calls.forEach((call, i) => {
          if (!call || typeof (call as Record<string, unknown>).name !== "string") {
            errors.push({ path: `expect.tool_calls.${i}.name`, message: "Field required." });
          }
        });
      }
    }
  }
  return errors;
}

export function validateFixtureJson(value: unknown): ValidationError[] {
  const errors: ValidationError[] = [];
  if (!value || typeof value !== "object" || Array.isArray(value)) return [{ path: "(root)", message: "Must be a JSON object." }];
  const v = value as Record<string, unknown>;
  if (typeof v.id !== "string") errors.push({ path: "id", message: "Field required." });
  if (!Array.isArray(v.tools)) errors.push({ path: "tools", message: "Must be a list." });
  if (!v.state || typeof v.state !== "object") errors.push({ path: "state", message: "Must be an object of collections." });
  return errors;
}
