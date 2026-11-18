"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { parse } from "yaml";
import { AlertTriangle, ArrowLeft, CheckCircle2, Loader2, Play, Save, Sparkles, XCircle } from "lucide-react";
import { CodeEditor } from "@/components/CodeEditor";
import { DRAFT_PREFIX } from "@/components/PanchitoDrawer";
import { Button, Card, ErrorBox, LinkButton, Loading, Tag } from "@/components/ui";
import { api } from "@/lib/api";
import { emitOnboardingChanged, openPanchito, useApi, usePageContext, useQueryParam } from "@/lib/hooks";
import type { ValidationError } from "@/lib/types";

const TEMPLATE = `id: my-new-scenario
title: Describe what this test checks
agent: acme-demo
language: en
risk: normal
tags: [booking]
fixture: acme-clinic.json
caller:
  mode: scripted
  voice: af_heart
  turns:
    - say: "Hi, I need an appointment on Tuesday at 10 AM."
    - say: "My name is Sam Rivera."
    - say: "Yes, that is correct."
expect:
  tool_calls:
    - name: book_appointment
      args: { name: "Sam Rivera", day: tuesday, time: "10:00" }
  latency_ms: { p50: 1500, p95: 2500 }
  judge:
    - "The agent confirmed the day and the time before booking."
limits: { max_turns: 12, max_seconds: 180, silence_timeout_s: 15 }
`;

function readDraft(id: string | null): string | null {
  if (!id) return null;
  try {
    return sessionStorage.getItem(`${DRAFT_PREFIX}scenario:${id}`);
  } catch {
    return null;
  }
}

interface Summary {
  id?: string;
  title?: string;
  risk?: string;
  mode?: string;
  turns?: number;
  checks: number;
}

function summarize(text: string): Summary | null {
  try {
    const p = parse(text) as Record<string, unknown> & {
      caller?: { mode?: string; turns?: unknown[] };
      expect?: Record<string, unknown> & { judge?: unknown[]; tool_calls?: unknown[] };
    };
    if (!p || typeof p !== "object") return null;
    const e = p.expect ?? {};
    return {
      id: typeof p.id === "string" ? p.id : undefined,
      title: typeof p.title === "string" ? p.title : undefined,
      risk: typeof p.risk === "string" ? p.risk : undefined,
      mode: p.caller?.mode,
      turns: Array.isArray(p.caller?.turns) ? p.caller.turns.length : undefined,
      checks: Object.keys(e).length + (Array.isArray(e.judge) ? e.judge.length - 1 : 0),
    };
  } catch {
    return null;
  }
}

export function ScenarioEditorView() {
  const routeId = useQueryParam("id");
  const isDraft = useQueryParam("draft") === "1";
  const isNew = useQueryParam("new") === "1" || !routeId || isDraft;
  usePageContext("scenario", { scenario_id: routeId ?? undefined });

  const loaded = useApi(async () => {
    if (isDraft) {
      const draft = readDraft(routeId);
      if (draft) return { yaml: draft, exists: false };
    }
    if (isNew || !routeId) return { yaml: TEMPLATE, exists: false };
    // after a save the URL loses draft=1 and points at the saved file
    const s = await api.scenario(routeId);
    return { yaml: s.yaml, exists: true };
  }, `${routeId}|${isNew}|${isDraft}`);

  const [edited, setEdited] = useState<string | null>(null);
  const text = edited ?? loaded.data?.yaml ?? "";
  const [validation, setValidation] = useState<{ ok: boolean; errors: ValidationError[]; checking: boolean; forText: string } | null>(null);
  const [saveState, setSaveState] = useState<{ kind: "idle" | "saving" | "saved" | "error"; message?: string }>({ kind: "idle" });
  const [runState, setRunState] = useState<{ kind: "idle" | "starting" | "started" | "error"; suiteId?: string; message?: string }>({ kind: "idle" });

  const summary = useMemo(() => summarize(text), [text]);
  const saveId = (isNew ? summary?.id : routeId) ?? "";

  // live validation, debounced
  useEffect(() => {
    if (!text) return;
    let alive = true;
    const t = setTimeout(() => {
      api
        .validateScenario(text)
        .then((res) => alive && setValidation({ ok: res.ok, errors: res.errors, checking: false, forText: text }))
        .catch((e) => alive && setValidation({ ok: false, errors: [{ path: "(server)", message: e instanceof Error ? e.message : String(e) }], checking: false, forText: text }));
    }, 450);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [text]);

  const checking = !validation || validation.forText !== text;
  const idMismatch = !isNew && summary?.id && routeId && summary.id !== routeId;

  async function save() {
    if (!saveId) {
      setSaveState({ kind: "error", message: "Set an id in the YAML first." });
      return;
    }
    setSaveState({ kind: "saving" });
    try {
      const res = await api.saveScenario(saveId, text);
      if (res.ok) {
        setSaveState({ kind: "saved" });
        if (isNew || !loaded.data?.exists) {
          api.markOnboarding("scenario_created").then(emitOnboardingChanged, () => undefined);
          window.history.replaceState(null, "", `/scenarios/edit/?id=${encodeURIComponent(saveId)}`);
        }
      } else {
        setSaveState({ kind: "error", message: res.errors.map((e) => `${e.path}: ${e.message}`).join("; ") });
      }
    } catch (e) {
      setSaveState({ kind: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }

  async function runIt() {
    if (!saveId) return;
    setRunState({ kind: "starting" });
    try {
      const res = await api.startSuite({ scenario_ids: [saveId] });
      setRunState({ kind: "started", suiteId: res.suite_id });
    } catch (e) {
      setRunState({ kind: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }

  if (loaded.error) return <ErrorBox message={loaded.error} onRetry={loaded.reload} />;
  if (!loaded.data) return <Loading label="Loading scenario" />;

  return (
    <div>
      <Link href="/scenarios/" className="mb-5 inline-flex items-center gap-1.5 text-sm text-muted hover:text-fg">
        <ArrowLeft size={15} aria-hidden /> Scenarios
      </Link>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="font-mono text-2xl font-semibold tracking-tight text-fg">{isNew ? summary?.id || "New scenario" : routeId}</h1>
            {isNew && <Tag tone="accent">{isDraft ? "draft from Panchito" : "new"}</Tag>}
            {summary?.risk === "critical" && <Tag tone="fail">critical</Tag>}
          </div>
          <p className="mt-1.5 text-sm text-muted">{summary?.title ?? "Edit the YAML. It is checked as you type."}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => openPanchito("Review this scenario and suggest better checks")}>
            <Sparkles size={16} className="text-accent" aria-hidden /> Ask Panchito
          </Button>
          <Button onClick={runIt} disabled={!saveId || isNew || runState.kind === "starting"}>
            {runState.kind === "starting" ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <Play size={16} aria-hidden />} Run this scenario
          </Button>
          <Button variant="primary" onClick={save} disabled={saveState.kind === "saving" || checking || !validation?.ok}>
            {saveState.kind === "saving" ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <Save size={16} aria-hidden />} Save
          </Button>
        </div>
      </div>

      {(saveState.kind === "saved" || saveState.kind === "error" || runState.kind === "started" || runState.kind === "error") && (
        <div className="mb-5 space-y-2" aria-live="polite">
          {saveState.kind === "saved" && (
            <div className="flex items-center gap-2 rounded-lg border border-pass/40 bg-pass-soft px-4 py-2.5 text-sm text-fg">
              <CheckCircle2 size={16} className="text-pass" aria-hidden /> Saved to scenarios/{saveId}.yaml
            </div>
          )}
          {saveState.kind === "error" && (
            <div role="alert" className="flex items-center gap-2 rounded-lg border border-fail/40 bg-fail-soft px-4 py-2.5 text-sm text-fg">
              <XCircle size={16} className="text-fail" aria-hidden /> Not saved: {saveState.message}
            </div>
          )}
          {runState.kind === "started" && (
            <div className="flex flex-wrap items-center gap-3 rounded-lg border border-accent/40 bg-accent-soft px-4 py-2.5 text-sm text-fg">
              <Play size={16} className="text-accent" aria-hidden /> Suite {runState.suiteId} started.
              <LinkButton href={`/suites/view/?id=${encodeURIComponent(runState.suiteId ?? "")}`} className="h-7 text-xs">
                Watch it
              </LinkButton>
            </div>
          )}
          {runState.kind === "error" && (
            <div role="alert" className="rounded-lg border border-fail/40 bg-fail-soft px-4 py-2.5 text-sm text-fg">
              Could not start: {runState.message}
            </div>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="card min-w-0 overflow-hidden">
          <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
            <span className="font-mono text-xs text-muted">scenarios/{saveId || "untitled"}.yaml</span>
            <span className="text-xs text-faint">{text.split("\n").length} lines</span>
          </div>
          <CodeEditor
            value={text}
            onChange={(v) => {
              setEdited(v);
              if (saveState.kind !== "idle") setSaveState({ kind: "idle" });
            }}
            language="yaml"
            label="Scenario YAML"
          />
        </div>
        <div className="space-y-6">
          <Card title="Validation">
            <div aria-live="polite">
              {checking ? (
                <div className="flex items-center gap-2 text-sm text-muted">
                  <Loader2 size={15} className="animate-spin" aria-hidden /> Checking...
                </div>
              ) : validation.ok ? (
                <div className="flex items-center gap-2 text-sm font-medium text-pass">
                  <CheckCircle2 size={16} aria-hidden /> Valid scenario
                </div>
              ) : (
                <>
                  <div className="flex items-center gap-2 text-sm font-medium text-fail">
                    <XCircle size={16} aria-hidden /> {validation.errors.length} problem{validation.errors.length > 1 ? "s" : ""}
                  </div>
                  <ul className="mt-3 space-y-2">
                    {validation.errors.map((e, i) => (
                      <li key={i} className="rounded-lg border border-border bg-surface-2 px-3 py-2">
                        <div className="font-mono text-xs text-fail">{e.path}</div>
                        <div className="mt-0.5 text-sm text-fg">{e.message}</div>
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {idMismatch && (
                <p className="mt-3 flex gap-2 text-xs text-warn">
                  <AlertTriangle size={14} className="shrink-0" aria-hidden /> The id in the file ({summary?.id}) is not the file name ({routeId}). The contract needs them to match.
                </p>
              )}
            </div>
          </Card>
          {summary && (
            <Card title="Summary">
              <dl className="grid grid-cols-2 gap-y-3 text-sm">
                <dt className="text-muted">Caller</dt>
                <dd className="text-fg">{summary.mode === "ai" ? "AI persona" : "Scripted"}</dd>
                <dt className="text-muted">Turns</dt>
                <dd className="tabular text-fg">{summary.turns ?? "set by the AI"}</dd>
                <dt className="text-muted">Risk</dt>
                <dd className="text-fg">{summary.risk ?? "n/a"}</dd>
                <dt className="text-muted">Checks</dt>
                <dd className="tabular text-fg">{summary.checks}</dd>
              </dl>
            </Card>
          )}
          <Card title="Tips">
            <ul className="list-disc space-y-1.5 pl-4 text-sm leading-relaxed text-muted">
              <li>
                Add <code className="font-mono text-xs text-fg">interrupt_after_ms</code> to a turn to test barge-in.
              </li>
              <li>
                <code className="font-mono text-xs text-fg">tool_calls.args</code> is a subset match.
              </li>
              <li>Each judge line is a yes or no question, answered with a quote.</li>
              <li>
                Critical scenarios must pass every run for the gate.
              </li>
            </ul>
          </Card>
        </div>
      </div>
    </div>
  );
}
