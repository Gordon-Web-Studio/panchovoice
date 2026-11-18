"use client";
import Link from "next/link";
import { useEffect } from "react";
import { CheckCircle2, Circle } from "lucide-react";
import { api } from "@/lib/api";
import { ONBOARDING_EVENT, useApi } from "@/lib/hooks";
import { STEPS } from "@/lib/onboarding";

export function useOnboarding() {
  const state = useApi(() => api.onboarding(), "onboarding");
  const { reload } = state;
  useEffect(() => {
    window.addEventListener(ONBOARDING_EVENT, reload);
    return () => window.removeEventListener(ONBOARDING_EVENT, reload);
  }, [reload]);
  return state;
}

export function OnboardingWidget() {
  const { data } = useOnboarding();
  if (!data) return null;
  const done = data.steps.filter((s) => s.done).length;
  const total = data.steps.length;
  if (done === total) return null;
  const next = STEPS.find((meta) => !data.steps.find((s) => s.id === meta.id)?.done);
  return (
    <Link
      href="/onboarding/"
      className="group block rounded-xl border border-border bg-surface-2 p-3.5 transition hover:border-border-strong"
      aria-label={`Getting started: ${done} of ${total} steps done`}
    >
      <div className="flex items-center justify-between text-xs">
        <span className="font-semibold text-fg">Getting started</span>
        <span className="tabular text-muted">
          {done}/{total}
        </span>
      </div>
      <div className="mt-2.5 flex gap-1" aria-hidden>
        {data.steps.map((s) => (
          <span key={s.id} className={`h-1.5 flex-1 rounded-full ${s.done ? "bg-accent" : "bg-surface-3"}`} />
        ))}
      </div>
      {next && (
        <div className="mt-3 flex items-start gap-2 text-xs text-muted group-hover:text-fg">
          <Circle size={13} className="mt-px shrink-0" aria-hidden />
          <span>
            Next: <span className="text-fg">{next.title}</span>
          </span>
        </div>
      )}
      {done > 0 && done < total && (
        <div className="sr-only">
          <CheckCircle2 aria-hidden /> {done} done
        </div>
      )}
    </Link>
  );
}
