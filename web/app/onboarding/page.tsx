"use client";
import { ArrowRight, Check, Sparkles } from "lucide-react";
import { useOnboarding } from "@/components/OnboardingWidget";
import { Button, cx, CopyCommand, ErrorBox, LinkButton, Loading, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";
import { emitOnboardingChanged, openPanchito, usePageContext } from "@/lib/hooks";
import { STEPS } from "@/lib/onboarding";

export default function OnboardingPage() {
  usePageContext("onboarding");
  const { data, error, reload } = useOnboarding();

  if (error) return <ErrorBox message={error} onRetry={reload} />;
  if (!data) return <Loading label="Loading checklist" />;

  const doneMap = new Map(data.steps.map((s) => [s.id, s.done]));
  const done = data.steps.filter((s) => s.done).length;
  const nextId = STEPS.find((s) => !doneMap.get(s.id))?.id;

  async function markDone(id: (typeof STEPS)[number]["id"]) {
    await api.markOnboarding(id).catch(() => undefined);
    emitOnboardingChanged();
  }

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="Getting started" subtitle="Six steps from zero to a CI gate on your own voice agent. Most take a minute." />
      <div className="card mb-6 flex items-center gap-5 px-6 py-5">
        <div className="relative h-14 w-14 shrink-0">
          <svg viewBox="0 0 36 36" className="h-14 w-14 -rotate-90" aria-hidden>
            <circle cx="18" cy="18" r="15.5" fill="none" stroke="var(--surface-3)" strokeWidth="3.5" />
            <circle
              cx="18"
              cy="18"
              r="15.5"
              fill="none"
              stroke="var(--accent)"
              strokeWidth="3.5"
              strokeLinecap="round"
              strokeDasharray={`${(done / data.steps.length) * 97.4} 97.4`}
            />
          </svg>
          <span className="tabular absolute inset-0 flex items-center justify-center text-sm font-semibold text-fg">
            {done}/{data.steps.length}
          </span>
        </div>
        <div className="flex-1">
          <div className="font-semibold text-fg">{done === data.steps.length ? "All done. Nice work." : `${data.steps.length - done} steps to go`}</div>
          <div className="text-sm text-muted">Panchito can help with any step. Ask in the chat.</div>
        </div>
        <Button onClick={() => openPanchito()}>
          <Sparkles size={16} className="text-accent" aria-hidden /> Ask Panchito
        </Button>
      </div>
      <ol className="space-y-3">
        {STEPS.map((s, i) => {
          const isDone = Boolean(doneMap.get(s.id));
          const isNext = s.id === nextId;
          return (
            <li key={s.id} className={cx("card px-5 py-5 transition", isNext && "border-accent/50 ring-1 ring-accent/20")}>
              <div className="flex gap-4">
                <div
                  className={cx(
                    "flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-semibold",
                    isDone ? "bg-accent text-accent-fg" : isNext ? "border-2 border-accent text-accent" : "border border-border-strong text-muted",
                  )}
                >
                  {isDone ? <Check size={16} aria-hidden /> : i + 1}
                  <span className="sr-only">{isDone ? "Done" : "Not done"}</span>
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className={cx("font-semibold", isDone ? "text-muted" : "text-fg")}>{s.title}</h2>
                    {isDone && <span className="text-xs font-medium text-pass">Done</span>}
                    {isNext && <span className="rounded-full bg-accent-soft px-2 py-0.5 text-xs font-medium text-accent">Next</span>}
                  </div>
                  <p className="mt-1 text-sm leading-relaxed text-muted">{s.why}</p>
                  {(!isDone || isNext) && (
                    <div className="mt-4 space-y-3">
                      {s.command && <CopyCommand command={s.command} />}
                      <div className="flex flex-wrap gap-2">
                        {s.action && (
                          <LinkButton href={s.action.href} variant={isNext ? "primary" : "secondary"} className="h-8 text-xs">
                            {s.action.label} <ArrowRight size={14} aria-hidden />
                          </LinkButton>
                        )}
                        {s.manual && !isDone && (
                          <Button className="h-8 text-xs" onClick={() => markDone(s.id)}>
                            <Check size={14} aria-hidden /> I did this
                          </Button>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
