"use client";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { CheckCircle2, Database, Loader2, Save, Sparkles, XCircle } from "lucide-react";
import { CodeEditor } from "@/components/CodeEditor";
import { DRAFT_PREFIX } from "@/components/PanchitoDrawer";
import { Button, Card, cx, Empty, ErrorBox, Loading, PageHeader, Table, Tag, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { openPanchito, useApi, usePageContext, useQueryParam } from "@/lib/hooks";
import type { FixtureJson } from "@/lib/types";

function cell(v: unknown) {
  if (v === null || v === undefined) return <span className="text-faint">n/a</span>;
  if (typeof v === "object") return <code className="font-mono text-xs">{JSON.stringify(v)}</code>;
  return String(v);
}

function CollectionTable({ name, rows }: { name: string; rows: Record<string, unknown>[] }) {
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <h3 className="font-mono text-sm font-medium text-fg">{name}</h3>
        <span className="text-xs text-muted">
          {rows.length} row{rows.length === 1 ? "" : "s"}
        </span>
      </div>
      <div className="overflow-hidden rounded-lg border border-border">
        {rows.length === 0 ? (
          <div className="px-4 py-3 text-sm text-muted">Empty collection</div>
        ) : (
          <Table>
            <thead>
              <tr>
                {cols.map((c) => (
                  <Th key={c}>{c}</Th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  {cols.map((c) => (
                    <Td key={c} className="tabular text-sm text-fg">
                      {cell(r[c])}
                    </Td>
                  ))}
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </div>
    </div>
  );
}

function readDraft(id: string | null): string | null {
  if (!id) return null;
  try {
    return sessionStorage.getItem(`${DRAFT_PREFIX}fixture:${id}`);
  } catch {
    return null;
  }
}

function FixtureEditor({ id, draft }: { id: string; draft: boolean }) {
  const loaded = useApi(async () => {
    const d = draft ? readDraft(id) : null;
    if (d) return { text: d, exists: false };
    const f = await api.fixture(id);
    return { text: JSON.stringify(f.json, null, 2), exists: true };
  }, `${id}|${draft}`);
  const [edited, setEdited] = useState<string | null>(null);
  const [tab, setTab] = useState<"table" | "json">("table");
  const [save, setSave] = useState<{ kind: "idle" | "saving" | "saved" | "error"; msg?: string }>({ kind: "idle" });
  const text = edited ?? loaded.data?.text ?? "";
  const parsed = useMemo((): { value: FixtureJson | null; error: string | null } => {
    try {
      return { value: JSON.parse(text) as FixtureJson, error: null };
    } catch (e) {
      return { value: null, error: e instanceof Error ? e.message : String(e) };
    }
  }, [text]);

  if (loaded.error) return <ErrorBox message={loaded.error} onRetry={loaded.reload} />;
  if (!loaded.data) return <Loading label="Loading fixture" />;

  async function doSave() {
    if (!parsed.value) return;
    setSave({ kind: "saving" });
    try {
      const res = await api.saveFixture(id, parsed.value);
      setSave(res.ok ? { kind: "saved" } : { kind: "error", msg: res.errors.map((e) => `${e.path}: ${e.message}`).join("; ") });
    } catch (e) {
      setSave({ kind: "error", msg: e instanceof Error ? e.message : String(e) });
    }
  }

  const f = parsed.value;
  return (
    <div className="min-w-0">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2.5">
            <h2 className="font-mono text-xl font-semibold text-fg">{id}</h2>
            {!loaded.data.exists && <Tag tone="accent">draft from Panchito</Tag>}
          </div>
          <p className="mt-1 text-sm text-muted">{f?.description ?? "Fixture"}</p>
        </div>
        <div className="flex items-center gap-2">
          <div role="tablist" aria-label="View" className="flex rounded-lg border border-border bg-surface p-0.5">
            {(["table", "json"] as const).map((t) => (
              <button
                key={t}
                role="tab"
                type="button"
                aria-selected={tab === t}
                onClick={() => setTab(t)}
                className={cx("rounded-md px-3 py-1.5 text-sm", tab === t ? "bg-surface-3 font-medium text-fg" : "text-muted hover:text-fg")}
              >
                {t === "table" ? "Table" : "JSON"}
              </button>
            ))}
          </div>
          <Button variant="primary" onClick={doSave} disabled={!parsed.value || save.kind === "saving"}>
            {save.kind === "saving" ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <Save size={16} aria-hidden />} Save
          </Button>
        </div>
      </div>
      <div aria-live="polite">
        {save.kind === "saved" && (
          <div className="mb-4 flex items-center gap-2 rounded-lg border border-pass/40 bg-pass-soft px-4 py-2.5 text-sm text-fg">
            <CheckCircle2 size={16} className="text-pass" aria-hidden /> Saved to fixtures/{id}.json
          </div>
        )}
        {save.kind === "error" && (
          <div role="alert" className="mb-4 flex items-center gap-2 rounded-lg border border-fail/40 bg-fail-soft px-4 py-2.5 text-sm text-fg">
            <XCircle size={16} className="text-fail" aria-hidden /> Not saved: {save.msg}
          </div>
        )}
        {parsed.error && (
          <div role="alert" className="mb-4 flex items-center gap-2 rounded-lg border border-fail/40 bg-fail-soft px-4 py-2.5 text-sm text-fg">
            <XCircle size={16} className="text-fail" aria-hidden /> JSON error: {parsed.error}
          </div>
        )}
      </div>
      {tab === "json" || !f ? (
        <div className="card overflow-hidden">
          <CodeEditor
            value={text}
            onChange={(v) => {
              setEdited(v);
              setSave({ kind: "idle" });
            }}
            language="json"
            label="Fixture JSON"
          />
        </div>
      ) : (
        <div className="space-y-6">
          <Card title={`Mock tools (${f.tools?.length ?? 0})`} padded={false}>
            <Table>
              <thead>
                <tr>
                  <Th>Tool</Th>
                  <Th>Action</Th>
                  <Th>Collection</Th>
                  <Th>Params</Th>
                </tr>
              </thead>
              <tbody>
                {(f.tools ?? []).map((t) => (
                  <tr key={t.name}>
                    <Td>
                      <div className="font-mono text-sm font-medium text-fg">{t.name}</div>
                      <div className="text-xs text-muted">{t.description}</div>
                    </Td>
                    <Td>
                      <Tag>{t.action ?? "n/a"}</Tag>
                    </Td>
                    <Td className="font-mono text-xs text-muted">{t.collection}</Td>
                    <Td>
                      <div className="flex flex-wrap gap-1">
                        {Object.entries(t.params ?? {}).map(([k, v]) => (
                          <code key={k} className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-xs text-fg">
                            {k}: <span className="text-muted">{v}</span>
                          </code>
                        ))}
                      </div>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Card>
          <Card title="State (seeds the mock backend and the caller facts)">
            <div className="space-y-6">
              {Object.entries(f.state ?? {}).map(([name, rows]) => (
                <CollectionTable key={name} name={name} rows={Array.isArray(rows) ? rows : []} />
              ))}
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}

export function FixturesView() {
  usePageContext("fixtures");
  const router = useRouter();
  const list = useApi(() => api.fixtures(), "fixtures");
  const qId = useQueryParam("id");
  const draft = useQueryParam("draft") === "1";
  const selected = qId ?? list.data?.[0]?.id ?? null;
  const items = [...(list.data ?? [])];
  if (qId && draft && !items.some((x) => x.id === qId)) items.unshift({ id: qId, description: "Draft from Panchito, not saved yet" });

  return (
    <div>
      <PageHeader
        title="Mock data"
        subtitle="A fixture is the one source of truth for the mock tools and for what the caller knows. The caller never makes up data during a call."
        actions={
          <Button onClick={() => openPanchito("Generate mock customers")}>
            <Sparkles size={16} className="text-accent" aria-hidden /> Generate with Panchito
          </Button>
        }
      />
      {list.error && <ErrorBox message={list.error} onRetry={list.reload} />}
      {!list.data && !list.error && <Loading label="Loading fixtures" />}
      {list.data && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[240px_minmax(0,1fr)]">
          <nav aria-label="Fixtures" className="flex flex-col gap-1">
            {items.length === 0 && <Empty icon={<Database size={22} />} title="No fixtures" />}
            {items.map((fx) => (
              <button
                key={fx.id}
                type="button"
                onClick={() => router.push(`/fixtures/?id=${encodeURIComponent(fx.id)}`)}
                aria-current={selected === fx.id ? "true" : undefined}
                className={cx(
                  "rounded-lg border px-3 py-2.5 text-left transition",
                  selected === fx.id ? "border-accent/50 bg-accent-soft" : "border-transparent hover:bg-surface-2",
                )}
              >
                <div className="flex items-center gap-2 font-mono text-sm text-fg">
                  <Database size={14} className={selected === fx.id ? "text-accent" : "text-muted"} aria-hidden />
                  {fx.id}
                </div>
                {fx.description && <div className="mt-0.5 line-clamp-2 text-xs text-muted">{fx.description}</div>}
              </button>
            ))}
          </nav>
          {selected ? <FixtureEditor key={`${selected}${draft}`} id={selected} draft={draft && selected === qId} /> : null}
        </div>
      )}
    </div>
  );
}
