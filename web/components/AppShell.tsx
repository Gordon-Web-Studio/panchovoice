"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import {
  Activity,
  Bot,
  Database,
  FileCode2,
  Gauge,
  Layers,
  ListChecks,
  Menu,
  Moon,
  Settings,
  Sun,
  X,
} from "lucide-react";
import { getMode } from "@/lib/api";
import { useMode } from "@/lib/hooks";
import { LogoMark } from "./Logo";
import { OnboardingWidget } from "./OnboardingWidget";
import { PanchitoDrawer } from "./PanchitoDrawer";
import { cx } from "./ui";

const NAV = [
  { href: "/", label: "Home", icon: Gauge },
  { href: "/runs/", label: "Runs", icon: Activity },
  { href: "/suites/", label: "Suites", icon: Layers },
  { href: "/scenarios/", label: "Scenarios", icon: FileCode2 },
  { href: "/fixtures/", label: "Mock data", icon: Database },
  { href: "/agents/", label: "Agents", icon: Bot },
  { href: "/onboarding/", label: "Getting started", icon: ListChecks },
  { href: "/settings/", label: "Settings", icon: Settings },
];

function isActive(path: string, href: string) {
  const p = path.endsWith("/") ? path : `${path}/`;
  if (href === "/") return p === "/" || p === "/welcome/";
  return p.startsWith(href);
}

// Theme lives on <html data-theme>. The inline script in layout.tsx sets it before paint.
const themeListeners = new Set<() => void>();
function useTheme(): ["dark" | "light", () => void] {
  const theme = useSyncExternalStore(
    (fn) => {
      themeListeners.add(fn);
      return () => {
        themeListeners.delete(fn);
      };
    },
    () => (document.documentElement.dataset.theme === "light" ? "light" : "dark"),
    () => "dark" as const,
  );
  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("pv-theme", next);
    } catch {
      /* ignore */
    }
    themeListeners.forEach((fn) => fn());
  };
  return [theme, toggle];
}

function ModeBadge() {
  const mode = useMode();
  if (mode !== "mock") return null;
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border border-warn/40 bg-warn-soft px-2.5 py-0.5 text-xs font-medium text-warn"
      title="The core API at port 8700 is not reachable, or ?mock=1 is set. The panel shows sample data."
    >
      <span className="h-1.5 w-1.5 rounded-full bg-warn" aria-hidden />
      Demo data
    </span>
  );
}

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const path = usePathname() ?? "/";
  const [theme, toggleTheme] = useTheme();
  return (
    <div className="flex h-full flex-col gap-6 px-4 py-5">
      <Link href="/" className="flex items-center gap-2.5 px-2" onClick={onNavigate}>
        <LogoMark size={30} />
        <span className="text-[15px] font-semibold tracking-tight text-fg">
          Pancho<span className="text-accent">Voice</span>AI
        </span>
      </Link>
      <nav aria-label="Main" className="flex flex-col gap-0.5">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = isActive(path, href);
          return (
            <Link
              key={href}
              href={href}
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              className={cx(
                "flex items-center gap-3 rounded-lg px-2.5 py-2 text-sm transition",
                active ? "bg-surface-2 font-medium text-fg" : "text-muted hover:bg-surface-2/60 hover:text-fg",
              )}
            >
              <Icon size={17} className={active ? "text-accent" : ""} aria-hidden />
              {label}
            </Link>
          );
        })}
      </nav>
      <div className="mt-auto flex flex-col gap-3">
        <OnboardingWidget />
        <div className="flex items-center justify-between px-1">
          <span className="text-xs text-faint">v0.1.0</span>
          <button
            type="button"
            onClick={toggleTheme}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2 text-xs text-muted hover:bg-surface-2 hover:text-fg"
            aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          >
            {theme === "dark" ? <Sun size={15} aria-hidden /> : <Moon size={15} aria-hidden />}
            {theme === "dark" ? "Light" : "Dark"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    getMode();
  }, []);
  return (
    <div className="flex min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-accent focus:px-3 focus:py-2 focus:text-accent-fg">
        Skip to content
      </a>
      <aside className="hidden w-64 shrink-0 border-r border-border bg-surface lg:block">
        <div className="sticky top-0 h-screen">
          <Sidebar />
        </div>
      </aside>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <div className="absolute inset-0 bg-black/50" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-72 border-r border-border bg-surface">
            <button
              type="button"
              className="absolute right-3 top-4 rounded-lg p-1.5 text-muted hover:bg-surface-2"
              onClick={() => setOpen(false)}
              aria-label="Close navigation"
            >
              <X size={18} />
            </button>
            <Sidebar onNavigate={() => setOpen(false)} />
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-bg/80 px-4 backdrop-blur lg:px-10">
          <button
            type="button"
            className="rounded-lg p-1.5 text-muted hover:bg-surface-2 lg:hidden"
            onClick={() => setOpen(true)}
            aria-label="Open navigation"
          >
            <Menu size={20} />
          </button>
          <div className="flex items-center gap-2 lg:hidden">
            <LogoMark size={24} />
            <span className="text-sm font-semibold">PanchoVoiceAI</span>
          </div>
          <div className="ml-auto flex items-center gap-3">
            <ModeBadge />
          </div>
        </header>
        <main id="main" className="mx-auto w-full max-w-[1240px] flex-1 px-4 pb-28 pt-8 lg:px-10">
          {children}
        </main>
      </div>
      <PanchitoDrawer />
    </div>
  );
}
