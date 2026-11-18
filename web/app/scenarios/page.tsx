"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import { FileCode2, Plus, Sparkles } from "lucide-react";
import { Button, Card, cx, Empty, ErrorBox, LinkButton, Loading, PageHeader, Table, Tag, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { openPanchito, useApi, usePageContext } from "@/lib/hooks";

export default function ScenariosPage() {
  usePageContext("scenarios");
  const list = useApi(() => api.scenarios(), "scenarios");
  const [tag, setTag] = useState<string | null>(null);
  const tags = useMemo(() => [...new Set((list.data ?? []).flatMap((s) => s.tags))].sort(), [list.data]);
  const rows = (list.data ?? []).filter((s) => !tag || s.tags.includes(tag));

  return (
    <div>
      <PageHeader
        title="Scenarios"
        subtitle="One YAML file per test. The caller follows the script (or plays a persona), and the expect block says what must happen."
        actions={
          <>
            <Button onClick={() => openPanchito("Write a scenario for an impatient caller")}>
              <Sparkles size={16} className="text-accent" aria-hidden /> Draft with Panchito
            </Button>
            <LinkButton href="/scenarios/edit/?new=1" variant="primary">
              <Plus size={16} aria-hidden /> New scenario
            </LinkButton>
          </>
        }
      />
      {tags.length > 0 && (
        <div className="mb-5 flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by tag">
          <span className="mr-1 text-xs font-medium text-muted">Tag</span>
          {[null, ...tags].map((t) => (
            <button
              key={t ?? "all"}
              type="button"
              aria-pressed={tag === t}
              onClick={() => setTag(t)}
              className={cx(
                "rounded-full border px-3 py-1 text-xs transition",
                tag === t ? "border-accent bg-accent-soft text-accent" : "border-border bg-surface text-muted hover:text-fg",
              )}
            >
              {t ?? "All"}
            </button>
          ))}
        </div>
      )}
      {list.error && <ErrorBox message={list.error} onRetry={list.reload} />}
      {!list.data && !list.error && <Loading label="Loading scenarios" />}
      {list.data && (
        <Card padded={false}>
          {rows.length === 0 ? (
            <Empty icon={<FileCode2 size={22} />} title="No scenarios here">
              Create one, or ask Panchito to draft it.
            </Empty>
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>Scenario</Th>
                  <Th>Risk</Th>
                  <Th>Tags</Th>
                  <Th>Language</Th>
                  <Th>Caller</Th>
                  <Th>
                    <span className="sr-only">Runs</span>
                  </Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => (
                  <tr key={s.id} className="hover:bg-surface-2/60">
                    <Td>
                      <Link href={`/scenarios/edit/?id=${encodeURIComponent(s.id)}`} className="block font-mono text-sm font-medium text-fg hover:text-accent">
                        {s.id}
                      </Link>
                      <div className="mt-0.5 text-xs text-muted">{s.title}</div>
                    </Td>
                    <Td>{s.risk === "critical" ? <Tag tone="fail">critical</Tag> : <Tag>normal</Tag>}</Td>
                    <Td>
                      <div className="flex flex-wrap gap-1">
                        {s.tags.map((t) => (
                          <Tag key={t}>{t}</Tag>
                        ))}
                      </div>
                    </Td>
                    <Td className="text-sm uppercase text-muted">{s.language}</Td>
                    <Td>{s.mode === "ai" ? <Tag tone="accent">AI persona</Tag> : <span className="text-sm text-muted">scripted</span>}</Td>
                    <Td className="text-right">
                      <Link href={`/runs/?scenario=${encodeURIComponent(s.id)}`} className="text-xs text-muted hover:text-accent">
                        Runs
                      </Link>
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
