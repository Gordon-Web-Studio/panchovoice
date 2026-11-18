"use client";
import { useRouter } from "next/navigation";
import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { Check, ChevronDown, ExternalLink, Loader2, Save, Send, Sparkles, X } from "lucide-react";
import { api } from "@/lib/api";
import { emitOnboardingChanged, PANCHITO_EVENT, useChatContext } from "@/lib/hooks";
import type { ChatMessage, Proposal } from "@/lib/types";
import { PanchitoAvatar } from "./Logo";
import { Button, cx, Tag } from "./ui";

interface Entry extends ChatMessage {
  proposals?: Proposal[];
  error?: boolean;
}

export const DRAFT_PREFIX = "pv-draft-";
const CLOSE_EVENT = "pv:panchito-close";

// Tiny renderer: paragraphs, **bold** and `code`. Enough for chat replies.
function Rich({ text }: { text: string }) {
  return (
    <>
      {text.split(/\n{2,}/).map((para, i) => (
        <p key={i} className="whitespace-pre-wrap [&:not(:first-child)]:mt-2.5">
          {para.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, j) => {
            if (part.startsWith("**") && part.endsWith("**")) return <strong key={j} className="font-semibold text-fg">{part.slice(2, -2)}</strong>;
            if (part.startsWith("`") && part.endsWith("`"))
              return (
                <code key={j} className="rounded bg-surface-3 px-1 py-px font-mono text-[12px] text-fg">
                  {part.slice(1, -1)}
                </code>
              );
            return <Fragment key={j}>{part}</Fragment>;
          })}
        </p>
      ))}
    </>
  );
}

function ProposalCard({ proposal }: { proposal: Proposal }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [errors, setErrors] = useState<string[]>([]);

  async function save() {
    setState("saving");
    setErrors([]);
    try {
      const res =
        proposal.kind === "scenario"
          ? await api.saveScenario(proposal.id, proposal.content)
          : await api.saveFixture(proposal.id, JSON.parse(proposal.content));
      if (res.ok) {
        setState("saved");
        if (proposal.kind === "scenario") {
          api.markOnboarding("scenario_created").then(emitOnboardingChanged, () => undefined);
        }
      } else {
        setState("error");
        setErrors(res.errors.map((e) => `${e.path}: ${e.message}`));
      }
    } catch (e) {
      setState("error");
      setErrors([e instanceof Error ? e.message : String(e)]);
    }
  }

  function openInEditor() {
    try {
      sessionStorage.setItem(DRAFT_PREFIX + proposal.kind + ":" + proposal.id, proposal.content);
    } catch {
      /* ignore */
    }
    window.dispatchEvent(new Event(CLOSE_EVENT));
    const path = proposal.kind === "scenario" ? "/scenarios/edit/" : "/fixtures/";
    router.push(`${path}?id=${encodeURIComponent(proposal.id)}&draft=1`);
  }

  return (
    <div className="mt-3 overflow-hidden rounded-xl border border-border bg-surface">
      <div className="flex items-start gap-3 px-3.5 py-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Tag tone="accent">{proposal.kind}</Tag>
            <span className="truncate font-mono text-xs text-fg">{proposal.id}</span>
          </div>
          <p className="mt-1.5 text-xs leading-relaxed text-muted">{proposal.summary}</p>
        </div>
      </div>
      <button
        type="button"
        className="flex w-full items-center gap-1.5 border-t border-border px-3.5 py-2 text-left text-xs text-muted hover:bg-surface-2 hover:text-fg"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <ChevronDown size={14} className={cx("transition", open && "rotate-180")} aria-hidden />
        {open ? "Hide preview" : "Show preview"}
      </button>
      {open && (
        <pre className="max-h-64 overflow-auto border-t border-border bg-bg px-3.5 py-3 font-mono text-[11.5px] leading-relaxed text-fg">
          {proposal.content}
        </pre>
      )}
      {errors.length > 0 && (
        <ul className="border-t border-border bg-fail-soft px-3.5 py-2 text-xs text-fail">
          {errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
      <div className="flex gap-2 border-t border-border px-3.5 py-2.5">
        <Button variant="primary" className="h-8 px-3 text-xs" onClick={save} disabled={state === "saving" || state === "saved"}>
          {state === "saving" ? <Loader2 size={14} className="animate-spin" aria-hidden /> : state === "saved" ? <Check size={14} aria-hidden /> : <Save size={14} aria-hidden />}
          {state === "saved" ? "Saved" : "Save"}
        </Button>
        <Button className="h-8 px-3 text-xs" onClick={openInEditor}>
          <ExternalLink size={14} aria-hidden /> Open in editor
        </Button>
      </div>
    </div>
  );
}

export function PanchitoDrawer() {
  const [open, setOpen] = useState(false);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const context = useChatContext();
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const entriesRef = useRef<Entry[]>([]);
  const contextRef = useRef(context);
  useEffect(() => {
    entriesRef.current = entries;
    contextRef.current = context;
  });

  const quick: string[] = [
    ...(context.run_id ? ["Why did this run fail?"] : []),
    "Write a scenario for an impatient caller",
    "Generate mock customers",
  ];

  async function send(text: string) {
    const content = text.trim();
    if (!content || busy) return;
    const history: Entry[] = [...entriesRef.current, { role: "user", content }];
    setEntries(history);
    setInput("");
    setBusy(true);
    try {
      const res = await api.chat(
        history.filter((e) => !e.error).map(({ role, content: c }) => ({ role, content: c })),
        contextRef.current,
      );
      setEntries((list) => [...list, { role: "assistant", content: res.reply, proposals: res.proposals }]);
    } catch (e) {
      setEntries((list) => [...list, { role: "assistant", content: `I could not answer: ${e instanceof Error ? e.message : String(e)}`, error: true }]);
    } finally {
      setBusy(false);
    }
  }
  const sendRef = useRef(send);
  useEffect(() => {
    sendRef.current = send;
  });

  useEffect(() => {
    function onOpen(e: Event) {
      setOpen(true);
      const prompt = (e as CustomEvent<{ prompt?: string }>).detail?.prompt;
      if (prompt) setTimeout(() => sendRef.current(prompt), 50);
    }
    const onClose = () => setOpen(false);
    window.addEventListener(PANCHITO_EVENT, onOpen);
    window.addEventListener(CLOSE_EVENT, onClose);
    return () => {
      window.removeEventListener(PANCHITO_EVENT, onOpen);
      window.removeEventListener(CLOSE_EVENT, onClose);
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [entries, busy]);

  const pageLabel: Record<string, string> = {
    home: "Home",
    run: "Run detail",
    runs: "Runs",
    scenario: "Scenario editor",
    scenarios: "Scenarios",
    fixtures: "Mock data",
    agents: "Agents",
    suites: "Suites",
    settings: "Settings",
    onboarding: "Getting started",
  };

  let body: ReactNode;
  if (entries.length === 0) {
    body = (
      <div className="flex flex-col items-center px-4 pt-8 text-center">
        <PanchitoAvatar size={52} />
        <h3 className="mt-4 text-base font-semibold text-fg">Hi, I am Panchito</h3>
        <p className="mt-1.5 max-w-xs text-sm leading-relaxed text-muted">
          I write scenarios, make mock data and explain failures. I only propose. Nothing is saved until you click Save.
        </p>
      </div>
    );
  } else {
    body = entries.map((e, i) => (
      <div key={i} className={cx("flex gap-2.5", e.role === "user" ? "justify-end" : "justify-start")}>
        {e.role === "assistant" && (
          <div className="mt-0.5 shrink-0">
            <PanchitoAvatar size={24} />
          </div>
        )}
        <div className={cx("min-w-0", e.role === "user" ? "max-w-[85%]" : "flex-1")}>
          <div
            className={cx(
              "rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed",
              e.role === "user" ? "rounded-br-md bg-accent text-accent-fg" : "rounded-tl-md bg-surface-2 text-muted",
              e.error && "border border-fail/40 text-fail",
            )}
          >
            {e.role === "user" ? e.content : <Rich text={e.content} />}
          </div>
          {e.proposals?.map((p) => <ProposalCard key={p.kind + p.id} proposal={p} />)}
        </div>
      </div>
    ));
  }

  return (
    <>
      {!open && (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="fixed bottom-4 right-4 z-40 inline-flex items-center gap-2.5 rounded-full border border-border bg-surface p-2 text-sm sm:bottom-6 sm:right-6 sm:pr-4 font-medium text-fg shadow-[var(--shadow)] transition hover:-translate-y-0.5 hover:border-accent/60"
          aria-label="Open Panchito, the AI companion"
        >
          <PanchitoAvatar size={30} />
          <span className="hidden sm:inline">Ask Panchito</span>
        </button>
      )}
      {open && (
        <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="false" aria-label="Panchito chat">
          <div className="absolute inset-0 bg-black/30 sm:bg-black/20" onClick={() => setOpen(false)} />
          <div className="relative flex h-full w-full flex-col border-l border-border bg-bg shadow-2xl sm:w-[440px]">
            <header className="flex items-center gap-3 border-b border-border px-4 py-3">
              <PanchitoAvatar size={30} />
              <div className="min-w-0 flex-1">
                <div className="text-sm font-semibold text-fg">Panchito</div>
                <div className="truncate text-xs text-muted">
                  Looking at: {pageLabel[context.page] ?? context.page}
                  {context.run_id ? ` (${context.run_id})` : context.scenario_id ? ` (${context.scenario_id})` : ""}
                </div>
              </div>
              <button type="button" onClick={() => setOpen(false)} className="rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-fg" aria-label="Close Panchito">
                <X size={18} />
              </button>
            </header>
            <div ref={listRef} className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 py-5" aria-live="polite">
              {body}
              {busy && (
                <div className="flex items-center gap-2 pl-8 text-xs text-muted">
                  <Loader2 size={14} className="animate-spin" aria-hidden /> Panchito is thinking...
                </div>
              )}
            </div>
            <div className="border-t border-border px-4 pb-4 pt-3">
              <div className="mb-3 flex flex-wrap gap-1.5">
                {quick.map((q) => (
                  <button
                    key={q}
                    type="button"
                    disabled={busy}
                    onClick={() => send(q)}
                    className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1 text-xs text-muted transition hover:border-accent/60 hover:text-fg disabled:opacity-50"
                  >
                    <Sparkles size={12} className="text-accent" aria-hidden />
                    {q}
                  </button>
                ))}
              </div>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  send(input);
                }}
                className="flex items-end gap-2 rounded-xl border border-border bg-surface p-2 focus-within:border-accent"
              >
                <label htmlFor="panchito-input" className="sr-only">
                  Message to Panchito
                </label>
                <textarea
                  id="panchito-input"
                  ref={inputRef}
                  rows={1}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      send(input);
                    }
                  }}
                  placeholder="Ask about a run, or describe a test..."
                  className="max-h-32 min-h-[36px] flex-1 resize-none bg-transparent px-2 py-1.5 text-sm text-fg placeholder:text-faint focus:outline-none"
                />
                <Button type="submit" variant="primary" className="h-9 w-9 px-0" disabled={busy || !input.trim()} aria-label="Send">
                  <Send size={16} aria-hidden />
                </Button>
              </form>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
