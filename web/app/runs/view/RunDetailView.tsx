"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { AlertTriangle, ArrowLeft, CheckCircle2, ChevronDown, Ear, FileCode2, Play, Quote, Scissors, Sparkles, Wrench, XCircle } from "lucide-react";
import { WaveformPlayer, type PlayerHandle } from "@/components/WaveformPlayer";
import { Button, Card, cx, Empty, ErrorBox, JsonBlock, LinkButton, Loading, Metric, StatusChip, Table, Tag, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { clock, dateTime, ms } from "@/lib/format";
import { emitOnboardingChanged, openPanchito, useApi, usePageContext, useQueryParam } from "@/lib/hooks";
import type { Check, RunResult, ToolCall, Turn } from "@/lib/types";

function latencyTone(v: number | null) {
  if (v === null) return "bg-surface-3 text-muted";
  if (v <= 1500) return "bg-pass-soft text-pass";
  if (v <= 2500) return "bg-warn-soft text-warn";
  return "bg-fail-soft text-fail";
}

function TurnRow({ turn, tools, onSeek }: { turn: Turn; tools: ToolCall[]; onSeek: (ms: number) => void }) {
  return (
    <li className="relative pl-10">
      <span className="absolute left-0 top-0 flex h-7 w-7 items-center justify-center rounded-full border border-border bg-surface-2 text-xs font-medium text-muted">
        {turn.index + 1}
      </span>
      <div className="space-y-2.5">
        <div className="flex flex-wrap items-start gap-3">
          <button
            type="button"
            onClick={() => onSeek(turn.caller_start_ms)}
            className="group min-w-0 flex-1 rounded-xl rounded-tl-sm border border-caller/25 bg-caller/8 px-3.5 py-2.5 text-left transition hover:border-caller/60"
            aria-label={`Play caller turn ${turn.index + 1} from ${clock(turn.caller_start_ms)}`}
          >
            <div className="mb-1 flex items-center gap-2 text-[11px] font-medium uppercase tracking-wide text-caller">
              Caller
              <span className="tabular font-mono normal-case tracking-normal text-faint">
                {clock(turn.caller_start_ms)} to {clock(turn.caller_end_ms)}
              </span>
              <Play size={11} className="opacity-0 transition group-hover:opacity-100" aria-hidden />
            </div>
            <div className="text-sm text-fg">{turn.caller_text}</div>
          </button>
        </div>
        <div className="flex items-start gap-2 pl-1 text-xs text-muted">
          <Ear size={13} className="mt-0.5 shrink-0" aria-hidden />
          <span>
            Agent heard: <span className="font-mono text-fg/80">{turn.agent_heard || "(nothing)"}</span>
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2 pl-1">
          <span className={cx("tabular inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium", latencyTone(turn.latency_ms))}>
            {ms(turn.latency_ms)} latency
          </span>
          {tools.map((t, i) => (
            <span key={i} className="inline-flex items-center gap-1.5 rounded-md border border-border bg-surface-2 px-2 py-0.5 font-mono text-xs text-fg">
              <Wrench size={12} className="text-muted" aria-hidden />
              {t.name}
              <span className="text-faint">at {clock(t.t_ms)}</span>
            </span>
          ))}
        </div>
        <button
          type="button"
          onClick={() => onSeek(turn.agent_start_ms)}
          className="group block w-full rounded-xl rounded-tl-sm border border-agent/25 bg-agent/8 px-3.5 py-2.5 text-left transition hover:border-agent/60"
          aria-label={`Play agent turn ${turn.index + 1} from ${clock(turn.agent_start_ms)}`}
        >
          <div className="mb-1 flex flex-wrap items-center gap-2 text-[11px] font-medium uppercase tracking-wide text-agent">
            Agent
            <span className="tabular font-mono normal-case tracking-normal text-faint">
              {clock(turn.agent_start_ms)} to {clock(turn.agent_end_ms)}
            </span>
            {turn.interrupted && (
              <span className="inline-flex items-center gap-1 rounded bg-warn-soft px-1.5 py-px normal-case tracking-normal text-warn">
                <Scissors size={11} aria-hidden /> Interrupted, stopped in {ms(turn.stop_ms)}
              </span>
            )}
            <Play size={11} className="opacity-0 transition group-hover:opacity-100" aria-hidden />
          </div>
          <div className={cx("text-sm text-fg", turn.interrupted && "opacity-80")}>
            {turn.agent_text}
            {turn.interrupted && <span className="text-warn"> ...</span>}
          </div>
        </button>
      </div>
    </li>
  );
}

function CheckItem({ check }: { check: Check }) {
  return (
    <li className={cx("rounded-xl border px-3.5 py-3", check.passed ? "border-border" : "border-fail/40 bg-fail-soft")}>
      <div className="flex items-start gap-2.5">
        {check.passed ? (
          <CheckCircle2 size={17} className="mt-px shrink-0 text-pass" aria-label="Passed" />
        ) : (
          <XCircle size={17} className="mt-px shrink-0 text-fail" aria-label="Failed" />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs font-medium text-fg">{check.id}</span>
            <Tag>{check.kind}</Tag>
            <span className={cx("text-xs font-medium", check.passed ? "text-pass" : "text-fail")}>{check.passed ? "Passed" : "Failed"}</span>
          </div>
          <p className="mt-1 text-sm leading-relaxed text-muted">{check.detail}</p>
          {check.evidence && (
            <blockquote className="mt-2 flex gap-2 rounded-lg border-l-2 border-accent bg-surface-2 px-3 py-2 text-sm italic text-fg">
              <Quote size={13} className="mt-1 shrink-0 text-accent" aria-hidden />
              <span>{check.evidence}</span>
            </blockquote>
          )}
        </div>
      </div>
    </li>
  );
}

function ToolCallsTable({ calls }: { calls: ToolCall[] }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!calls.length) return <Empty icon={<Wrench size={22} />} title="No tool calls in this run" />;
  return (
    <Table>
      <thead>
        <tr>
          <Th>Time</Th>
          <Th>Name</Th>
          <Th>Args</Th>
          <Th>Response</Th>
        </tr>
      </thead>
      <tbody>
        {calls.map((c, i) => {
          const resp = c.response as { ok?: boolean } | null;
          return (
            <tr key={i} className="align-top">
              <Td className="tabular whitespace-nowrap font-mono text-xs text-muted">{clock(c.t_ms)}</Td>
              <Td className="whitespace-nowrap font-mono text-sm font-medium text-fg">{c.name}</Td>
              <Td>
                <code className="block max-w-md whitespace-pre-wrap break-all font-mono text-xs text-fg">{JSON.stringify(c.args)}</code>
              </Td>
              <Td>
                <button
                  type="button"
                  className="inline-flex items-center gap-1.5 text-xs text-muted hover:text-fg"
                  onClick={() => setOpen(open === i ? null : i)}
                  aria-expanded={open === i}
                >
                  {resp?.ok === false ? <Tag tone="fail">ok: false</Tag> : resp?.ok ? <Tag tone="accent">ok: true</Tag> : <Tag>response</Tag>}
                  <ChevronDown size={13} className={cx("transition", open === i && "rotate-180")} aria-hidden />
                </button>
                {open === i && <JsonBlock value={c.response} className="mt-2 max-w-md" />}
              </Td>
            </tr>
          );
        })}
      </tbody>
    </Table>
  );
}

function toolsForTurn(run: RunResult, i: number) {
  const turn = run.turns[i];
  const next = run.turns[i + 1];
  const from = turn.caller_start_ms;
  const to = next ? next.caller_start_ms : Infinity;
  return run.tool_calls.filter((t) => t.t_ms >= from && t.t_ms < to);
}

export function RunDetailView() {
  const id = useQueryParam("id") ?? "";
  usePageContext("run", { run_id: id || undefined });
  const run = useApi(() => (id ? api.run(id) : Promise.reject(new Error("No run id in the URL."))), id);
  const player = useRef<PlayerHandle>(null);
  const marked = useRef(false);

  useEffect(() => {
    if (run.data && !marked.current) {
      marked.current = true;
      api.markOnboarding("viewed_report").then(emitOnboardingChanged, () => undefined);
    }
  }, [run.data]);

  if (run.error) return <ErrorBox message={run.error} onRetry={run.reload} />;
  if (!run.data) return <Loading label="Loading run" />;
  const r = run.data;
  const failed = r.checks.filter((c) => !c.passed);
  const seek = (msValue: number) => {
    player.current?.seek(msValue, true);
    document.getElementById("recording")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };

  return (
    <div>
      <Link href="/runs/" className="mb-5 inline-flex items-center gap-1.5 text-sm text-muted hover:text-fg">
        <ArrowLeft size={15} aria-hidden /> Runs
      </Link>
      <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-[26px] font-semibold tracking-tight text-fg">{r.scenario_id}</h1>
            <StatusChip status={r.status} />
          </div>
          <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-sm text-muted">
            <span className="font-mono text-xs leading-5">{r.run_id}</span>
            <span>
              Suite{" "}
              <Link href={`/suites/view/?id=${encodeURIComponent(r.suite_id)}`} className="font-mono text-xs text-fg hover:text-accent">
                {r.suite_id}
              </Link>
            </span>
            <span>Agent {r.agent_id}</span>
            <span>{dateTime(r.started_at)}</span>
            <span>{clock(r.duration_ms)} long</span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <LinkButton href={`/scenarios/edit/?id=${encodeURIComponent(r.scenario_id)}`}>
            <FileCode2 size={16} aria-hidden /> Scenario
          </LinkButton>
          {r.status !== "passed" && (
            <Button variant="primary" onClick={() => openPanchito(r.status === "error" ? "Why did this run fail? It is an error." : "Why did this run fail?")}>
              <Sparkles size={16} aria-hidden /> Why did this fail?
            </Button>
          )}
        </div>
      </div>

      {r.status === "error" && (
        <div role="alert" className="mb-6 flex gap-3 rounded-xl border border-warn/40 bg-warn-soft px-5 py-4">
          <AlertTriangle size={20} className="mt-0.5 shrink-0 text-warn" aria-hidden />
          <div>
            <div className="font-medium text-fg">Harness error: the test could not run</div>
            <p className="mt-1 text-sm text-muted">{r.error}</p>
            <p className="mt-2 text-sm text-muted">This is not a failure of the agent. It is not counted in the pass rate. Check the agent with `pv doctor`, then run again.</p>
          </div>
        </div>
      )}

      {failed.length > 0 && (
        <div role="alert" className="mb-6 rounded-xl border border-fail/40 bg-fail-soft px-5 py-4">
          <div className="flex items-center gap-2 font-medium text-fg">
            <XCircle size={18} className="text-fail" aria-hidden /> {failed.length} check{failed.length > 1 ? "s" : ""} failed
          </div>
          <ul className="mt-2 space-y-1 text-sm text-muted">
            {failed.map((c) => (
              <li key={c.id}>
                <span className="font-mono text-xs text-fg">{c.id}</span>: {c.detail}
              </li>
            ))}
          </ul>
        </div>
      )}

      {r.status !== "error" && (
        <>
          <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
            <Metric label="Latency P50" value={ms(r.metrics.latency_p50_ms)} />
            <Metric label="Latency P95" value={ms(r.metrics.latency_p95_ms)} />
            <Metric label="Latency max" value={ms(r.metrics.latency_max_ms)} />
            <Metric label="WER" value={r.metrics.wer === null ? "n/a" : r.metrics.wer.toFixed(2)} hint="what the agent heard" />
            <Metric label="Barge-in stop" value={ms(r.metrics.barge_in_stop_ms)} hint={r.metrics.barge_in_stop_ms === null ? "no interruption in this run" : "time to go quiet"} />
            <Metric label="Cost" value={`$${r.cost.usd_estimate.toFixed(2)}`} hint={`${r.cost.llm_calls} LLM calls`} />
          </div>

          <div id="recording" className="mb-6 scroll-mt-20">
            <Card title="Recording" action={<span className="text-xs text-muted">Click the wave to seek. Click a turn to play it.</span>}>
              <WaveformPlayer ref={player} runId={r.run_id} turns={r.turns} durationMs={r.duration_ms} />
            </Card>
          </div>

          <div className="mb-6 grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
            <Card title={`Turns (${r.turns.length})`}>
              {r.turns.length ? (
                <ol className="space-y-6">
                  {r.turns.map((t, i) => (
                    <TurnRow key={t.index} turn={t} tools={toolsForTurn(r, i)} onSeek={seek} />
                  ))}
                </ol>
              ) : (
                <Empty title="No turns recorded" />
              )}
            </Card>
            <Card title={`Checks (${r.checks.length - failed.length} of ${r.checks.length} passed)`}>
              <ul className="space-y-2.5">
                {[...failed, ...r.checks.filter((c) => c.passed)].map((c) => (
                  <CheckItem key={c.id} check={c} />
                ))}
              </ul>
            </Card>
          </div>

          <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
            <Card title={`Tool calls (${r.tool_calls.length})`} padded={false}>
              <ToolCallsTable calls={r.tool_calls} />
            </Card>
            <Card title="Final mock state">
              <JsonBlock value={r.final_state} className="max-h-80" />
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
