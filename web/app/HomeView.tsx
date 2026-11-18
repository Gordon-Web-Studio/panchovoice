"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Play, Sparkles } from "lucide-react";
import { RunTable } from "@/components/RunTable";
import { Welcome } from "@/components/Welcome";
import { Button, Card, ErrorBox, GateChip, LinkButton, Loading, Metric, PageHeader, StatusChip } from "@/components/ui";
import { api } from "@/lib/api";
import { ago, ms, pct } from "@/lib/format";
import { emitOnboardingChanged, openPanchito, useApi, usePageContext, useQueryParam } from "@/lib/hooks";
import type { SuiteDetail, SuiteSummary } from "@/lib/types";

function GateBar({ rate, threshold }: { rate: number | null; threshold: number }) {
  const value = rate ?? 0;
  const ok = rate !== null && rate >= threshold;
  return (
    <div>
      <div className="relative h-3 overflow-visible rounded-full bg-surface-3">
        <div className={`h-3 rounded-full ${ok ? "bg-pass" : "bg-fail"}`} style={{ width: `${value * 100}%` }} />
        <div className="absolute -top-1.5 h-6 w-0.5 rounded bg-fg" style={{ left: `${threshold * 100}%` }} aria-hidden />
      </div>
      <div className="relative mt-2 h-4 text-[11px] text-muted">
        <span className="absolute left-0">0%</span>
        <span className="absolute -translate-x-1/2 whitespace-nowrap font-medium text-fg" style={{ left: `${threshold * 100}%` }}>
          gate {pct(threshold)}
        </span>
      </div>
    </div>
  );
}

function Trend({ suites, threshold }: { suites: SuiteSummary[]; threshold: number }) {
  const items = suites.filter((s) => s.pass_rate !== null).slice(0, 10).reverse();
  const W = 100;
  const H = 100;
  if (!items.length) return <p className="text-sm text-muted">No finished suites yet.</p>;
  const barW = W / items.length;
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-56 w-full xl:h-[340px]" role="img" aria-label="Pass rate of recent suites">
        {items.map((s, i) => {
          const h = (s.pass_rate ?? 0) * (H - 4);
          const ok = s.gate_passed;
          return (
            <rect
              key={s.suite_id}
              x={i * barW + barW * 0.18}
              y={H - h}
              width={barW * 0.64}
              height={h}
              rx={1.2}
              fill={ok ? "var(--pass)" : "var(--fail)"}
              opacity={i === items.length - 1 ? 1 : 0.55}
            >
              <title>{`${s.suite_id}: ${pct(s.pass_rate)} (${ok ? "gate passed" : "gate failed"})`}</title>
            </rect>
          );
        })}
        <line x1={0} x2={W} y1={H - threshold * (H - 4)} y2={H - threshold * (H - 4)} stroke="var(--fg)" strokeWidth={0.6} strokeDasharray="2 1.5" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="mt-2 flex justify-between text-[11px] text-muted">
        <span>{ago(items[0].started_at)}</span>
        <span>Dashed line: gate {pct(threshold)}</span>
        <span>latest</span>
      </div>
    </div>
  );
}

function RunningSuite({ id, onDone }: { id: string; onDone: () => void }) {
  const [suite, setSuite] = useState<SuiteDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const onDoneRef = useRef(onDone);
  useEffect(() => {
    onDoneRef.current = onDone;
  });
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const s = await api.suite(id);
        if (!alive) return;
        setSuite(s);
        if (s.status === "running") timer = setTimeout(poll, 1500);
        else {
          api.markOnboarding("first_run").then(emitOnboardingChanged, () => undefined);
          onDoneRef.current();
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    };
    poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [id]);

  const doneCount = suite?.runs.length ?? 0;
  const total = suite?.total ?? 0;
  const running = !suite || suite.status === "running";
  return (
    <Card className="mb-6 border-accent/40">
      <div className="flex flex-wrap items-center gap-4">
        <StatusChip status={running ? "running" : suite.gate_passed ? "passed" : "failed"} label={running ? "Suite running" : "Suite finished"} />
        <span className="font-mono text-xs text-muted">{id}</span>
        <span className="tabular ml-auto text-sm text-muted">
          {doneCount} of {total || "?"} runs
        </span>
        {!running && (
          <LinkButton href={`/suites/view/?id=${encodeURIComponent(id)}`} variant="primary" className="h-8 text-xs">
            Open suite
          </LinkButton>
        )}
      </div>
      <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-surface-3">
        <div className="h-full rounded-full bg-accent transition-all duration-700" style={{ width: total ? `${(doneCount / total) * 100}%` : "6%" }} />
      </div>
      {error && <p className="mt-3 text-sm text-fail">{error}</p>}
    </Card>
  );
}

export function HomeView() {
  usePageContext("home");
  const wantRun = useQueryParam("run") === "1";
  const [refresh, setRefresh] = useState(0);
  const stats = useApi(() => api.stats(), `stats${refresh}`);
  const suites = useApi(() => api.suites(), `suites${refresh}`);
  const runs = useApi(() => api.runs({ limit: 8 }), `runs${refresh}`);
  const config = useApi(() => api.config(), "config");
  const [runningId, setRunningId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const runBtn = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (wantRun) runBtn.current?.focus();
  }, [wantRun]);

  const threshold = config.data?.gate?.pass_rate ?? 0.95;

  async function startSuite() {
    setStarting(true);
    setStartError(null);
    try {
      const res = await api.startSuite({});
      setRunningId(res.suite_id);
    } catch (e) {
      setStartError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  }

  if (stats.error) return <ErrorBox message={stats.error} onRetry={stats.reload} />;
  if (!stats.data || !suites.data || !runs.data) return <Loading label="Loading dashboard" />;

  if (stats.data.runs === 0 && !runningId) {
    return (
      <div className="space-y-6">
        <Welcome />
        <div className="text-center">
          <Button variant="primary" onClick={startSuite} disabled={starting} ref={runBtn}>
            <Play size={16} aria-hidden /> Run the demo suite now
          </Button>
          {startError && <p className="mt-3 text-sm text-fail">{startError}</p>}
        </div>
      </div>
    );
  }

  const last = suites.data.find((s) => s.status !== "running") ?? stats.data.last_suite;

  return (
    <div>
      <PageHeader
        title="Home"
        subtitle="Health of your voice agent test suite. Pass rate excludes harness errors; they are shown apart."
        actions={
          <>
            <Button onClick={() => openPanchito("Why did the last suite fail?")}>
              <Sparkles size={16} className="text-accent" aria-hidden /> Explain last suite
            </Button>
            <Button variant="primary" onClick={startSuite} disabled={starting || Boolean(runningId)} ref={runBtn}>
              <Play size={16} aria-hidden /> {starting ? "Starting..." : "Run suite"}
            </Button>
          </>
        }
      />
      {startError && (
        <div className="mb-6">
          <ErrorBox message={`Could not start the suite: ${startError}`} />
        </div>
      )}
      {runningId && (
        <RunningSuite
          id={runningId}
          onDone={() => {
            setRefresh((r) => r + 1);
          }}
        />
      )}

      {last && (
        <Card className="mb-6">
          <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)] lg:items-center">
            <div>
              <div className="flex flex-wrap items-center gap-2.5">
                <h2 className="text-sm font-semibold text-fg">Last suite</h2>
                <GateChip passed={last.gate_passed} />
                <Link href={`/suites/view/?id=${encodeURIComponent(last.suite_id)}`} className="font-mono text-xs text-muted hover:text-accent">
                  {last.suite_id}
                </Link>
              </div>
              <div className="mt-4 flex items-baseline gap-3">
                <span className={`tabular text-5xl font-semibold tracking-tight ${last.gate_passed ? "text-pass" : "text-fail"}`}>{pct(last.pass_rate, 1)}</span>
                <span className="text-sm text-muted">pass rate, {ago(last.started_at)}</span>
              </div>
              <div className="mt-5">
                <GateBar rate={last.pass_rate} threshold={threshold} />
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div className="rounded-xl bg-pass-soft px-4 py-3">
                <div className="tabular text-2xl font-semibold text-pass">{last.passed}</div>
                <div className="text-xs text-muted">passed</div>
              </div>
              <div className="rounded-xl bg-fail-soft px-4 py-3">
                <div className="tabular text-2xl font-semibold text-fail">{last.failed}</div>
                <div className="text-xs text-muted">failed</div>
              </div>
              <div className="rounded-xl bg-warn-soft px-4 py-3">
                <div className="tabular text-2xl font-semibold text-warn">{last.errors}</div>
                <div className="text-xs text-muted">harness errors</div>
              </div>
              <p className="col-span-3 text-xs leading-relaxed text-muted">
                {last.gate_passed
                  ? "The gate passed. CI would let this change merge."
                  : `The gate failed. ${last.failed ? `${last.failed} run${last.failed > 1 ? "s" : ""} failed` : "No run failed"}${last.errors ? ` and ${last.errors} could not run` : ""}. CI would block this change.`}
              </p>
            </div>
          </div>
        </Card>
      )}

      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Metric label="Pass rate, 7 days" value={pct(stats.data.pass_rate_7d, 1)} tone={(stats.data.pass_rate_7d ?? 0) >= threshold ? "pass" : "fail"} hint={`gate ${pct(threshold)}`} />
        <Metric label="Latency P50" value={ms(stats.data.latency_p50_7d)} hint="voice to voice, 7 days" />
        <Metric label="Latency P95" value={ms(stats.data.latency_p95_7d)} hint="voice to voice, 7 days" />
        <Metric label="Runs" value={stats.data.runs} hint={`${stats.data.suites} suites`} />
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <Card title="Last runs" action={<Link href="/runs/" className="text-xs text-muted hover:text-accent">All runs</Link>} padded={false}>
          <RunTable runs={runs.data} compact />
        </Card>
        <Card title="Pass rate by suite">
          <Trend suites={suites.data} threshold={threshold} />
        </Card>
      </div>
    </div>
  );
}
