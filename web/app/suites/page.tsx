"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ChevronRight, Layers } from "lucide-react";
import { Card, Empty, ErrorBox, GateChip, Loading, PageHeader, StatusChip, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { ago, dateTime, pct } from "@/lib/format";
import { useApi, usePageContext } from "@/lib/hooks";

export default function SuitesPage() {
  usePageContext("suites");
  const router = useRouter();
  const suites = useApi(() => api.suites(), "suites");
  return (
    <div>
      <PageHeader title="Suites" subtitle="A suite is one batch of runs. The gate decides if CI passes: pass rate over the line, every critical scenario passed, and no harness errors." />
      {suites.error && <ErrorBox message={suites.error} onRetry={suites.reload} />}
      {!suites.data && !suites.error && <Loading label="Loading suites" />}
      {suites.data && (
        <Card padded={false}>
          {suites.data.length === 0 ? (
            <Empty icon={<Layers size={22} />} title="No suites yet">
              Run one from Home or with `pv run`.
            </Empty>
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>Suite</Th>
                  <Th>Started</Th>
                  <Th>Gate</Th>
                  <Th className="text-right">Pass rate</Th>
                  <Th className="text-right">Passed</Th>
                  <Th className="text-right">Failed</Th>
                  <Th className="text-right">Errors</Th>
                  <Th>
                    <span className="sr-only">Open</span>
                  </Th>
                </tr>
              </thead>
              <tbody>
                {suites.data.map((s) => (
                  <tr key={s.suite_id} className="cursor-pointer hover:bg-surface-2/60" onClick={() => router.push(`/suites/view/?id=${encodeURIComponent(s.suite_id)}`)}>
                    <Td>
                      <Link href={`/suites/view/?id=${encodeURIComponent(s.suite_id)}`} className="font-mono text-sm text-fg hover:text-accent" onClick={(e) => e.stopPropagation()}>
                        {s.suite_id}
                      </Link>
                    </Td>
                    <Td className="text-sm text-muted">
                      {dateTime(s.started_at)} <span className="text-faint">({ago(s.started_at)})</span>
                    </Td>
                    <Td>{s.status === "running" ? <StatusChip status="running" /> : <GateChip passed={s.gate_passed} />}</Td>
                    <Td className="tabular text-right font-medium text-fg">{pct(s.pass_rate, 1)}</Td>
                    <Td className="tabular text-right text-pass">{s.passed}</Td>
                    <Td className="tabular text-right text-fail">{s.failed}</Td>
                    <Td className="tabular text-right text-warn">{s.errors}</Td>
                    <Td className="w-8 text-faint">
                      <ChevronRight size={16} aria-hidden />
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
      )}
    </div>
  );
}
