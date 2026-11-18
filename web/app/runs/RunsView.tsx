"use client";
import { useMemo, useState } from "react";
import { AlertTriangle, Search } from "lucide-react";
import { RunTable } from "@/components/RunTable";
import { Card, cx, Empty, ErrorBox, inputCls, Loading, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";
import { useApi, usePageContext, useQueryParam } from "@/lib/hooks";
import type { RunStatus } from "@/lib/types";

type Filter = "all" | RunStatus;

export function RunsView() {
  usePageContext("runs");
  const initial = useQueryParam("status") as Filter | null;
  const initialScenario = useQueryParam("scenario") ?? "";
  const [filter, setFilter] = useState<Filter>(initial ?? "all");
  const [scenario, setScenario] = useState(initialScenario);
  const [q, setQ] = useState("");
  const runs = useApi(() => api.runs({ limit: 500 }), "runs");

  const scenarios = useMemo(() => [...new Set((runs.data ?? []).map((r) => r.scenario_id))].sort(), [runs.data]);
  const base = useMemo(
    () => (runs.data ?? []).filter((r) => (!scenario || r.scenario_id === scenario) && (!q || `${r.run_id} ${r.scenario_id} ${r.suite_id}`.includes(q.toLowerCase()))),
    [runs.data, scenario, q],
  );
  const counts = {
    all: base.length,
    passed: base.filter((r) => r.status === "passed").length,
    failed: base.filter((r) => r.status === "failed").length,
    error: base.filter((r) => r.status === "error").length,
  };
  const judged = base.filter((r) => r.status !== "error" && (filter === "all" || r.status === filter));
  const errors = base.filter((r) => r.status === "error" && (filter === "all" || filter === "error"));

  const chips: { id: Filter; label: string; cls: string }[] = [
    { id: "all", label: "All", cls: "" },
    { id: "passed", label: "Passed", cls: "text-pass" },
    { id: "failed", label: "Failed", cls: "text-fail" },
    { id: "error", label: "Errors", cls: "text-warn" },
  ];

  return (
    <div>
      <PageHeader title="Runs" subtitle="Every recorded call. Errors are harness problems (agent down, timeout) and never count as agent failures." />
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <div role="group" aria-label="Filter by status" className="flex rounded-lg border border-border bg-surface p-0.5">
          {chips.map((c) => (
            <button
              key={c.id}
              type="button"
              aria-pressed={filter === c.id}
              onClick={() => setFilter(c.id)}
              className={cx(
                "rounded-md px-3 py-1.5 text-sm transition",
                filter === c.id ? "bg-surface-3 font-medium text-fg" : "text-muted hover:text-fg",
              )}
            >
              {c.label} <span className={cx("tabular ml-1 text-xs", c.cls || "text-faint")}>{counts[c.id]}</span>
            </button>
          ))}
        </div>
        <label className="sr-only" htmlFor="scenario-filter">
          Scenario
        </label>
        <select id="scenario-filter" value={scenario} onChange={(e) => setScenario(e.target.value)} className={inputCls}>
          <option value="">All scenarios</option>
          {scenarios.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <div className="relative">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" aria-hidden />
          <label className="sr-only" htmlFor="run-search">
            Search runs
          </label>
          <input id="run-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search run or suite id" className={cx(inputCls, "w-64 pl-9")} />
        </div>
      </div>

      {runs.error && <ErrorBox message={runs.error} onRetry={runs.reload} />}
      {!runs.data && !runs.error && <Loading label="Loading runs" />}
      {runs.data && (
        <div className="space-y-6">
          {filter !== "error" && (
            <Card padded={false}>
              {judged.length ? <RunTable runs={judged} showSuite /> : <Empty title="No runs match these filters" />}
            </Card>
          )}
          {errors.length > 0 && (
            <Card
              padded={false}
              title={
                <span className="inline-flex items-center gap-2">
                  <AlertTriangle size={15} className="text-warn" aria-hidden /> Harness errors ({errors.length}), not counted in the pass rate
                </span>
              }
            >
              <RunTable runs={errors} showSuite />
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
