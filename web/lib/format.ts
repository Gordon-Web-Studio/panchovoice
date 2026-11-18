export function ms(value: number | null | undefined): string {
  if (value === null || value === undefined) return "n/a";
  if (value >= 10000) return `${(value / 1000).toFixed(1)} s`;
  return `${Math.round(value).toLocaleString("en-US")} ms`;
}

export function pct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return "n/a";
  return `${(value * 100).toFixed(digits)}%`;
}

export function clock(msValue: number): string {
  const total = Math.max(0, msValue / 1000);
  const m = Math.floor(total / 60);
  const s = total - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "n/a";
  const diff = Date.now() - new Date(iso).getTime();
  const min = Math.round(diff / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const h = Math.round(min / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.round(h / 24)} days ago`;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "n/a";
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function json(value: unknown): string {
  return JSON.stringify(value, null, 2);
}
