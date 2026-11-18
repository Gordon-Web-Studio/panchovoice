"use client";
import Link from "next/link";
import { useEffect } from "react";
import { ArrowLeft, CheckCircle2, XCircle } from "lucide-react";
import { RunTable } from "@/components/RunTable";
import { Card, ErrorBox, GateChip, Loading, Metric, StatusChip } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime, pct } from "@/lib/format";
import { useApi, usePageContext, useQueryParam } from "@/lib/hooks";

export function SuiteDetailView() {
  const id = useQueryParam("id") ?? "";
  usePageContext("suites");
  const suite = useApi(() => (id ? api.suite(id) : Promise.reject(new Error("No suite id in the URL."))), id);
  const config = useApi(() => api.config(), "config");
  const scenarios = useApi(() => api.scenarios(), "scenarios");
  const { reload } = suite;
  const running = suite.data?.status === "running";

  useEffect(() => {
    if (!running) return;
    const t = setInterval(reload, 1500);
    return () => clearInterval(t);
  }, [running, reload]);

  if (suite.error) return <ErrorBox message={suite.error} onRetry={suite.reload} />;
  if (!suite.data) return <Loading label="Loading suite" />;
  const s = suite.data;
  const gate = config.data?.gate ?? { pass_rate: 0.95, critical_must_pass_all: true };
  const rateOk = s.pass_rate !== null && s.pass_rate >= gate.pass_rate;
  const critical = new Set((scenarios.data ?? []).filter((x) => x.risk === "critical").map((x) => x.id));
  const criticalBad = s.runs.filter((r) => critical.has(r.scenario_id) && r.status !== "passed");

  const rules = [
    { ok: rateOk, text: `Pass rate ${pct(s.pass_rate, 1)} is ${rateOk ? "at or over" : "under"} the line of ${pct(gate.pass_rate)}` },
    { ok: s.errors === 0, text: s.errors === 0 ? "No harness errors" : `${s.errors} harness error${s.errors > 1 ? "s" : ""} (the gate needs 0)` },
    ...(gate.critical_must_pass_all
      ? [
          {
            ok: criticalBad.length === 0,
            text: criticalBad.length === 0 ? "Every run of every critical scenario passed" : `Critical scenario did not pass: ${[...new Set(criticalBad.map((r) => r.scenario_id))].join(", ")}`,
          },
        ]
      : []),
  ];

  return (
    <div>
      <Link href="/suites/" className="mb-5 inline-flex items-center gap-1.5 text-sm text-muted hover:text-fg">
        <ArrowLeft size={15} aria-hidden /> Suites
      </Link>
      <div className="mb-8 flex flex-wrap items-center gap-3">
        <h1 className="font-mono text-2xl font-semibold tracking-tight text-fg">{s.suite_id}</h1>
        {running ? <StatusChip status="running" /> : <GateChip passed={s.gate_passed} />}
        <span className="text-sm text-muted">{dateTime(s.started_at)}</span>
      </div>
      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-5">
        <Metric label="Pass rate" value={pct(s.pass_rate, 1)} tone={s.pass_rate === null ? undefined : rateOk ? "pass" : "fail"} hint={`gate ${pct(gate.pass_rate)}`} />
        <Metric label="Runs" value={`${s.runs.length}${running ? ` of ${s.total}` : ""}`} />
        <Metric label="Passed" value={s.passed} tone="pass" />
        <Metric label="Failed" value={s.failed} tone={s.failed ? "fail" : undefined} />
        <Metric label="Harness errors" value={s.errors} tone={s.errors ? "warn" : undefined} hint="not in the pass rate" />
      </div>
      {!running && (
        <Card title="Gate result" className="mb-6">
          <ul className="space-y-2">
            {rules.map((r) => (
              <li key={r.text} className="flex items-center gap-2.5 text-sm">
                {r.ok ? <CheckCircle2 size={16} className="text-pass" aria-label="Met" /> : <XCircle size={16} className="text-fail" aria-label="Not met" />}
                <span className={r.ok ? "text-muted" : "text-fg"}>{r.text}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
      <Card title="Runs" padded={false}>
        <RunTable runs={s.runs} />
      </Card>
    </div>
  );
}
