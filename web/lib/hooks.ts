"use client";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useSearchParams } from "next/navigation";
import { currentMode, subscribeMode, type Mode } from "./api";
import type { ChatContext } from "./types";

export interface Loadable<T> {
  data: T | undefined;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/** Runs an async loader on mount and when `key` changes. */
export function useApi<T>(loader: () => Promise<T>, key: string = ""): Loadable<T> {
  const [state, setState] = useState<{ data: T | undefined; error: string | null; loadedKey: string | null }>({
    data: undefined,
    error: null,
    loadedKey: null,
  });
  const [tick, setTick] = useState(0);
  const loaderRef = useRef(loader);
  useEffect(() => {
    loaderRef.current = loader;
  });

  useEffect(() => {
    let alive = true;
    loaderRef
      .current()
      .then((data) => alive && setState({ data, error: null, loadedKey: key + tick }))
      .catch((e: unknown) => alive && setState((s) => ({ data: s.data, error: e instanceof Error ? e.message : String(e), loadedKey: key + tick })));
    return () => {
      alive = false;
    };
  }, [key, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data: state.data, error: state.error, loading: state.loadedKey !== key + tick, reload };
}

export function useMode(): Mode | null {
  return useSyncExternalStore(subscribeMode, currentMode, () => null);
}

/** Query param reader. Pages that use it must sit inside a <Suspense> boundary. */
export function useQueryParam(name: string): string | null {
  return useSearchParams().get(name);
}

// ---- events between widgets ----
export const ONBOARDING_EVENT = "pv:onboarding-changed";
export function emitOnboardingChanged() {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(ONBOARDING_EVENT));
}

// ---- Panchito page context ----
let chatContext: ChatContext = { page: "home" };
const contextListeners = new Set<() => void>();
export function setChatContext(ctx: ChatContext) {
  chatContext = ctx;
  contextListeners.forEach((fn) => fn());
}
export function useChatContext(): ChatContext {
  return useSyncExternalStore(
    (fn) => {
      contextListeners.add(fn);
      return () => {
        contextListeners.delete(fn);
      };
    },
    () => chatContext,
    () => chatContext,
  );
}
/** Pages call this so Panchito knows where the user is. */
export function usePageContext(page: string, extra: Omit<ChatContext, "page"> = {}) {
  const runId = extra.run_id;
  const scenarioId = extra.scenario_id;
  useEffect(() => {
    setChatContext({ page, ...(runId ? { run_id: runId } : {}), ...(scenarioId ? { scenario_id: scenarioId } : {}) });
  }, [page, runId, scenarioId]);
}

// ---- open Panchito from anywhere ----
export const PANCHITO_EVENT = "pv:panchito-open";
export function openPanchito(prompt?: string) {
  window.dispatchEvent(new CustomEvent(PANCHITO_EVENT, { detail: { prompt } }));
}
