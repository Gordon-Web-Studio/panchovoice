"use client";
import Link from "next/link";
import { useState, type ComponentProps, type ReactNode } from "react";
import { AlertTriangle, Check, CheckCircle2, CircleDashed, Copy, Loader2, XCircle } from "lucide-react";
import type { RunStatus } from "@/lib/types";

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(" ");
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-[26px] font-semibold tracking-tight text-fg">{title}</h1>
        {subtitle && <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Card({ children, className, title, action, padded = true }: { children: ReactNode; className?: string; title?: ReactNode; action?: ReactNode; padded?: boolean }) {
  return (
    <section className={cx("card min-w-0", className)}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 border-b border-border px-5 py-3.5">
          <h2 className="text-sm font-semibold text-fg">{title}</h2>
          {action}
        </header>
      )}
      <div className={padded ? "p-5" : ""}>{children}</div>
    </section>
  );
}

type Variant = "primary" | "secondary" | "ghost" | "danger";
const variants: Record<Variant, string> = {
  primary: "bg-accent text-accent-fg hover:brightness-110 shadow-sm",
  secondary: "bg-surface-2 text-fg border border-border hover:bg-surface-3 hover:border-border-strong",
  ghost: "text-muted hover:text-fg hover:bg-surface-2",
  danger: "bg-fail-soft text-fail border border-transparent hover:border-fail",
};
const base =
  "inline-flex items-center justify-center gap-2 rounded-lg px-3.5 h-9 text-sm font-medium transition disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap";

export function Button({ variant = "secondary", className, ...props }: ComponentProps<"button"> & { variant?: Variant }) {
  return <button type="button" className={cx(base, variants[variant], className)} {...props} />;
}

export function LinkButton({ variant = "secondary", className, ...props }: ComponentProps<typeof Link> & { variant?: Variant }) {
  return <Link className={cx(base, variants[variant], className)} {...props} />;
}

const STATUS: Record<string, { label: string; cls: string; icon: ReactNode }> = {
  passed: { label: "Passed", cls: "bg-pass-soft text-pass", icon: <CheckCircle2 size={14} aria-hidden /> },
  failed: { label: "Failed", cls: "bg-fail-soft text-fail", icon: <XCircle size={14} aria-hidden /> },
  error: { label: "Error", cls: "bg-warn-soft text-warn", icon: <AlertTriangle size={14} aria-hidden /> },
  running: { label: "Running", cls: "bg-accent-soft text-accent", icon: <Loader2 size={14} className="animate-spin" aria-hidden /> },
  pending: { label: "Pending", cls: "bg-surface-3 text-muted", icon: <CircleDashed size={14} aria-hidden /> },
};

export function StatusChip({ status, label }: { status: RunStatus | "running" | "pending" | string; label?: string }) {
  const s = STATUS[status] ?? STATUS.pending;
  return (
    <span className={cx("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium", s.cls)}>
      {s.icon}
      {label ?? s.label}
    </span>
  );
}

export function GateChip({ passed }: { passed: boolean | null }) {
  if (passed === null) return <StatusChip status="pending" label="Gate n/a" />;
  return <StatusChip status={passed ? "passed" : "failed"} label={passed ? "Gate passed" : "Gate failed"} />;
}

export function Tag({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "accent" | "warn" | "fail" }) {
  const tones = {
    neutral: "bg-surface-2 text-muted border-border",
    accent: "bg-accent-soft text-accent border-transparent",
    warn: "bg-warn-soft text-warn border-transparent",
    fail: "bg-fail-soft text-fail border-transparent",
  };
  return <span className={cx("inline-flex items-center whitespace-nowrap rounded-md border px-1.5 py-px text-[11px] font-medium", tones[tone])}>{children}</span>;
}

export function Metric({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "pass" | "fail" | "warn" }) {
  const toneCls = tone === "pass" ? "text-pass" : tone === "fail" ? "text-fail" : tone === "warn" ? "text-warn" : "text-fg";
  return (
    <div className="card px-5 py-4">
      <div className="text-xs font-medium uppercase tracking-wide text-muted">{label}</div>
      <div className={cx("tabular mt-2 text-2xl font-semibold tracking-tight", toneCls)}>{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("animate-pulse rounded-lg bg-surface-2", className)} aria-hidden />;
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-10 text-sm text-muted" role="status">
      <Loader2 size={16} className="animate-spin" aria-hidden /> {label}...
    </div>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex items-start gap-3 rounded-xl border border-fail/40 bg-fail-soft px-4 py-3 text-sm text-fg">
      <XCircle size={18} className="mt-0.5 shrink-0 text-fail" aria-hidden />
      <div className="flex-1">
        <div className="font-medium">Something went wrong</div>
        <div className="mt-0.5 text-muted">{message}</div>
      </div>
      {onRetry && (
        <Button variant="ghost" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      {icon && <div className="mb-3 text-faint">{icon}</div>}
      <div className="text-sm font-medium text-fg">{title}</div>
      {children && <div className="mt-1 max-w-sm text-sm text-muted">{children}</div>}
    </div>
  );
}

export function CopyCommand({ command, label }: { command: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-center gap-2 rounded-lg border border-border bg-bg py-1.5 pl-3 pr-1.5 font-mono text-[13px]">
      <span className="select-none text-faint" aria-hidden>
        $
      </span>
      <code className="min-w-0 flex-1 truncate text-fg">{command}</code>
      <button
        type="button"
        aria-label={label ?? `Copy command ${command}`}
        className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-xs text-muted hover:bg-surface-2 hover:text-fg"
        onClick={() => {
          navigator.clipboard?.writeText(command).catch(() => undefined);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
      >
        {copied ? <Check size={14} aria-hidden /> : <Copy size={14} aria-hidden />}
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cx("relative overflow-x-auto", className)}>
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  );
}

export function Th({ children, className }: { children?: ReactNode; className?: string }) {
  return <th className={cx("border-b border-border px-4 py-2.5 text-left text-xs font-medium uppercase tracking-wide text-muted", className)}>{children}</th>;
}

export function Td({ children, className }: { children?: ReactNode; className?: string }) {
  return <td className={cx("border-b border-border px-4 py-3 align-middle", /text-right/.test(className ?? "") && "whitespace-nowrap", className)}>{children}</td>;
}

export function JsonBlock({ value, className }: { value: unknown; className?: string }) {
  return (
    <pre className={cx("overflow-auto rounded-lg border border-border bg-bg p-3 font-mono text-[12.5px] leading-relaxed text-fg", className)}>
      {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function Field({ label, children, htmlFor }: { label: string; children: ReactNode; htmlFor?: string }) {
  return (
    <label htmlFor={htmlFor} className="flex flex-col gap-1.5 text-xs font-medium text-muted">
      {label}
      {children}
    </label>
  );
}

export const inputCls =
  "h-9 rounded-lg border border-border bg-surface-2 px-3 text-sm text-fg placeholder:text-faint hover:border-border-strong focus:border-accent focus:outline-none";
