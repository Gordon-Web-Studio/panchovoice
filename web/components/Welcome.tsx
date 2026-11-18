"use client";
import { ArrowRight, AudioWaveform, Gavel, Timer, Wrench } from "lucide-react";
import { openPanchito } from "@/lib/hooks";
import { STEPS } from "@/lib/onboarding";
import { LogoMark } from "./Logo";
import { Button, CopyCommand, LinkButton } from "./ui";

export function Welcome() {
  return (
    <div className="mx-auto max-w-3xl">
      <div className="card relative overflow-hidden px-8 py-10 sm:px-12">
        <div
          className="pointer-events-none absolute -right-24 -top-24 h-72 w-72 rounded-full opacity-40 blur-3xl"
          style={{ background: "radial-gradient(circle, var(--accent) 0%, transparent 70%)" }}
          aria-hidden
        />
        <LogoMark size={44} />
        <h1 className="mt-6 text-3xl font-semibold tracking-tight text-fg">Welcome to PanchoVoiceAI</h1>
        <p className="mt-3 max-w-xl text-[15px] leading-relaxed text-muted">
          Test your voice agent the way your customers use it: a caller agent phones it with real audio, records both sides, and
          scores the call. No runs yet, so let us start with the demo agent.
        </p>
        <div className="mt-8 grid grid-cols-1 gap-3 sm:grid-cols-2">
          {[
            { icon: Timer, t: "Voice to voice latency", d: "P50 and P95 from the recording, per turn" },
            { icon: AudioWaveform, t: "Barge-in and STT", d: "How fast it stops, and what it heard (WER)" },
            { icon: Wrench, t: "Tool calls and state", d: "Exact args against a mock backend" },
            { icon: Gavel, t: "LLM judge", d: "Yes or no checks, each with a quote" },
          ].map(({ icon: Icon, t, d }) => (
            <div key={t} className="flex gap-3 rounded-xl border border-border bg-surface-2 p-3.5">
              <Icon size={18} className="mt-0.5 shrink-0 text-accent" aria-hidden />
              <div>
                <div className="text-sm font-medium text-fg">{t}</div>
                <div className="text-xs text-muted">{d}</div>
              </div>
            </div>
          ))}
        </div>
        <div className="mt-8 space-y-3">
          <div className="text-sm font-medium text-fg">Start in two commands</div>
          <CopyCommand command={STEPS[0].command!} />
          <CopyCommand command={STEPS[1].command!} />
        </div>
        <div className="mt-8 flex flex-wrap gap-2">
          <LinkButton href="/onboarding/" variant="primary">
            Open the checklist <ArrowRight size={16} aria-hidden />
          </LinkButton>
          <Button onClick={() => openPanchito()}>Ask Panchito</Button>
        </div>
      </div>
    </div>
  );
}
