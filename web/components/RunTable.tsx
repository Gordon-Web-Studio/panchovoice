"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ChevronRight } from "lucide-react";
import { ago, ms } from "@/lib/format";
import type { RunSummary } from "@/lib/types";
import { StatusChip, Table, Td, Th } from "./ui";

export function runHref(id: string) {
  return `/runs/view/?id=${encodeURIComponent(id)}`;
}

export function RunTable({ runs, showSuite = false, compact = false }: { runs: RunSummary[]; showSuite?: boolean; compact?: boolean }) {
  const router = useRouter();
  return (
    <Table>
      <thead>
        <tr>
          <Th>Status</Th>
          <Th>Scenario</Th>
          {!compact && <Th>Run</Th>}
          {showSuite && <Th>Suite</Th>}
          <Th className="text-right">P50</Th>
          <Th className="text-right">P95</Th>
          {!compact && <Th className="text-right">WER</Th>}
          <Th className="text-right">Started</Th>
          <Th>
            <span className="sr-only">Open</span>
          </Th>
        </tr>
      </thead>
      <tbody>
        {runs.map((r) => (
          <tr key={r.run_id} className="group cursor-pointer transition hover:bg-surface-2/60" onClick={() => router.push(runHref(r.run_id))}>
            <Td>
              <StatusChip status={r.status} />
            </Td>
            <Td className="whitespace-nowrap">
              <Link href={runHref(r.run_id)} className="font-medium text-fg hover:text-accent" onClick={(e) => e.stopPropagation()}>
                {r.scenario_id}
              </Link>
            </Td>
            {!compact && <Td className="font-mono text-xs text-muted">{r.run_id}</Td>}
            {showSuite && <Td className="font-mono text-xs text-muted">{r.suite_id}</Td>}
            <Td className="tabular text-right text-muted">{ms(r.latency_p50_ms)}</Td>
            <Td className="tabular text-right text-muted">{ms(r.latency_p95_ms)}</Td>
            {!compact && <Td className="tabular text-right text-muted">{r.wer === null ? "n/a" : r.wer.toFixed(2)}</Td>}
            <Td className="whitespace-nowrap text-right text-xs text-muted">{ago(r.started_at)}</Td>
            <Td className="w-8 text-faint">
              <ChevronRight size={16} className="transition group-hover:translate-x-0.5 group-hover:text-fg" aria-hidden />
            </Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}
