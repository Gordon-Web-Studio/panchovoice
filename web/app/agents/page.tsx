"use client";
import { Bot, CheckCircle2, Plug, RefreshCw, XCircle } from "lucide-react";
import { Button, Card, CopyCommand, Empty, ErrorBox, JsonBlock, Loading, PageHeader, Tag } from "@/components/ui";
import { api } from "@/lib/api";
import { useApi, usePageContext } from "@/lib/hooks";

const SNIPPET = `agents:
  - id: acme-demo
    name: ACME Appointments (demo)
    adapter: websocket
    url: ws://localhost:8710/ws
    language: en
  - id: my-agent              # your agent
    name: My voice agent
    adapter: websocket        # speaks pv-ws/1
    url: ws://localhost:9000/ws
    language: en`;

export default function AgentsPage() {
  usePageContext("agents");
  const agents = useApi(() => api.agents(), "agents");
  return (
    <div>
      <PageHeader
        title="Agents"
        subtitle="The voice agents under test. PanchoVoiceAI calls each one over a WebSocket and sends real audio."
        actions={
          <Button onClick={agents.reload}>
            <RefreshCw size={16} aria-hidden /> Check again
          </Button>
        }
      />
      {agents.error && <ErrorBox message={agents.error} onRetry={agents.reload} />}
      {!agents.data && !agents.error && <Loading label="Checking agents" />}
      {agents.data && (
        <div className="mb-8 grid grid-cols-1 gap-4 md:grid-cols-2">
          {agents.data.length === 0 && (
            <Card className="md:col-span-2">
              <Empty icon={<Bot size={22} />} title="No agents configured">
                Add one to panchovoice.yaml (see below).
              </Empty>
            </Card>
          )}
          {agents.data.map((a) => (
            <div key={a.id} className="card p-5">
              <div className="flex items-start gap-3.5">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-surface-2">
                  <Bot size={20} className="text-accent" aria-hidden />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="font-semibold text-fg">{a.name}</h2>
                    {a.reachable ? (
                      <span className="inline-flex items-center gap-1 rounded-full bg-pass-soft px-2 py-0.5 text-xs font-medium text-pass">
                        <CheckCircle2 size={13} aria-hidden /> Reachable
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 rounded-full bg-fail-soft px-2 py-0.5 text-xs font-medium text-fail">
                        <XCircle size={13} aria-hidden /> Not reachable
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 font-mono text-xs text-muted">{a.id}</div>
                </div>
              </div>
              <dl className="mt-5 grid grid-cols-[90px_1fr] gap-y-2 text-sm">
                <dt className="text-muted">Adapter</dt>
                <dd>
                  <Tag>{a.adapter}</Tag>
                </dd>
                <dt className="text-muted">URL</dt>
                <dd className="truncate font-mono text-xs leading-5 text-fg">{a.url}</dd>
                <dt className="text-muted">Language</dt>
                <dd className="uppercase text-fg">{a.language}</dd>
              </dl>
              {!a.reachable && (
                <p className="mt-4 rounded-lg bg-surface-2 px-3 py-2 text-xs leading-relaxed text-muted">
                  Nothing answers at this URL. Start the agent, or run <code className="font-mono text-fg">pv doctor</code> to see what is missing.
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      <Card
        title={
          <span id="add" className="inline-flex scroll-mt-24 items-center gap-2">
            <Plug size={16} className="text-accent" aria-hidden /> Add an agent
          </span>
        }
      >
        <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
          <ol className="space-y-4 text-sm leading-relaxed text-muted">
            <li>
              <span className="font-medium text-fg">1. Speak the pv-ws/1 protocol.</span> Your agent is a WebSocket server. It gets PCM16 audio at
              16 kHz, sends audio back, and sends JSON for transcripts, tool calls and barge-in (<code className="font-mono text-fg">clear</code>).
              See docs/architecture/contract.md, section 5.
            </li>
            <li>
              <span className="font-medium text-fg">2. Call the mock tools.</span> Use the <code className="font-mono text-fg">mock_tools_url</code>{" "}
              from the start message, so every tool call is recorded with its exact args.
            </li>
            <li>
              <span className="font-medium text-fg">3. Add it to panchovoice.yaml</span> under <code className="font-mono text-fg">agents</code>. The panel
              cannot edit this file yet.
            </li>
            <li>
              <span className="font-medium text-fg">4. Check it.</span> Restart the server and press &quot;Check again&quot;.
              <div className="mt-2 space-y-2">
                <CopyCommand command="pv doctor" />
                <CopyCommand command="pv run --agent my-agent --tag booking" />
              </div>
            </li>
          </ol>
          <div>
            <div className="mb-2 font-mono text-xs text-muted">panchovoice.yaml</div>
            <JsonBlock value={SNIPPET} />
          </div>
        </div>
      </Card>
    </div>
  );
}
