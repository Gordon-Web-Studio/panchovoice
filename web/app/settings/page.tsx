"use client";
import type { ReactNode } from "react";
import { Brain, Ear, Gauge, Mic, Server } from "lucide-react";
import { Card, CopyCommand, ErrorBox, JsonBlock, Loading, PageHeader } from "@/components/ui";
import { apiBase } from "@/lib/api";
import { api } from "@/lib/api";
import { pct } from "@/lib/format";
import { useApi, useMode, usePageContext } from "@/lib/hooks";

function Row({ k, v }: { k: string; v: ReactNode }) {
  return (
    <>
      <dt className="text-sm text-muted">{k}</dt>
      <dd className="font-mono text-sm text-fg">{v ?? "n/a"}</dd>
    </>
  );
}

function Section({ icon, title, children, note, id }: { icon: ReactNode; title: string; children: ReactNode; note: ReactNode; id?: string }) {
  return (
    <Card
      title={
        <span id={id} className="inline-flex scroll-mt-24 items-center gap-2">
          {icon} {title}
        </span>
      }
    >
      <dl className="grid grid-cols-[140px_1fr] gap-x-4 gap-y-2.5">{children}</dl>
      <p className="mt-4 border-t border-border pt-3 text-xs leading-relaxed text-muted">{note}</p>
    </Card>
  );
}

export default function SettingsPage() {
  usePageContext("settings");
  const config = useApi(() => api.config(), "config");
  const health = useApi(() => api.health(), "health");
  const mode = useMode();
  const c = config.data;

  return (
    <div>
      <PageHeader
        title="Settings"
        subtitle={
          <>
            Read only. Settings live in <code className="font-mono text-fg">panchovoice.yaml</code>. Edit the file, then restart{" "}
            <code className="font-mono text-fg">pv serve</code>.
          </>
        }
      />
      {config.error && <ErrorBox message={config.error} onRetry={config.reload} />}
      {!c && !config.error && <Loading label="Loading settings" />}
      {c && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <Section
            icon={<Brain size={16} className="text-accent" aria-hidden />}
            title="LLM (caller, judge, Panchito)"
            note={
              <>
                <code className="font-mono text-fg">claude-cli</code> uses the <code className="font-mono text-fg">claude -p</code> command on your
                subscription. Other choices: <code className="font-mono text-fg">anthropic</code> (API key in the environment) or{" "}
                <code className="font-mono text-fg">ollama</code> (local).
              </>
            }
          >
            <Row k="Provider" v={c.llm?.provider} />
            <Row k="Model" v={c.llm?.model} />
            <Row k="Budget" v={c.llm?.budget_usd !== undefined ? `$${c.llm.budget_usd}` : undefined} />
            <Row k="Max calls / run" v={c.llm?.max_calls_per_run} />
          </Section>
          <Section
            id="gate"
            icon={<Gauge size={16} className="text-accent" aria-hidden />}
            title="CI gate"
            note={
              <>
                The gate passes when the pass rate is at or over the line, no harness error happened, and (when on) every critical scenario
                passed. In CI, run:
                <span className="mt-2 block">
                  <CopyCommand command="pv run --gate --junit report.xml" />
                </span>
              </>
            }
          >
            <Row k="Pass rate line" v={c.gate ? pct(c.gate.pass_rate) : undefined} />
            <Row k="Critical must pass" v={c.gate ? (c.gate.critical_must_pass_all ? "yes" : "no") : undefined} />
          </Section>
          <Section
            icon={<Mic size={16} className="text-accent" aria-hidden />}
            title="Text to speech (caller voice)"
            note="Kokoro runs locally as an OpenAI compatible HTTP server. Start it with Docker compose."
          >
            <Row k="Provider" v={c.tts?.provider} />
            <Row k="URL" v={c.tts?.url} />
            <Row k="Status" v={health.data ? (health.data.checks.tts ? "up" : "down") : "checking"} />
          </Section>
          <Section
            icon={<Ear size={16} className="text-accent" aria-hidden />}
            title="Speech to text (scoring)"
            note="faster-whisper turns the recording into text for WER and the judge. Bigger models are more exact and slower."
          >
            <Row k="Provider" v={c.stt?.provider} />
            <Row k="Model" v={c.stt?.model} />
            <Row k="Status" v={health.data ? (health.data.checks.stt ? "up" : "down") : "checking"} />
          </Section>
          <Section
            icon={<Server size={16} className="text-accent" aria-hidden />}
            title="Panel connection"
            note={
              <>
                Set <code className="font-mono text-fg">NEXT_PUBLIC_API_URL</code> at build time to point the panel at another server. Add{" "}
                <code className="font-mono text-fg">?mock=1</code> to any URL for demo data, <code className="font-mono text-fg">?mock=0</code> to leave it.
              </>
            }
          >
            <Row k="API" v={apiBase() || "same origin"} />
            <Row k="Mode" v={mode === "mock" ? "demo data (mock)" : mode === "live" ? "live" : "checking"} />
            <Row k="Server version" v={health.data?.version} />
            <Row k="Data dir" v={c.data_dir} />
          </Section>
          <Card title="Raw config">
            <JsonBlock value={c} className="max-h-96" />
          </Card>
        </div>
      )}
    </div>
  );
}
