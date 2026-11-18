"use client";
import dynamic from "next/dynamic";
import { useMemo, useSyncExternalStore } from "react";
import { yaml } from "@codemirror/lang-yaml";
import { json } from "@codemirror/lang-json";
import { EditorView } from "@codemirror/view";

const CodeMirror = dynamic(() => import("@uiw/react-codemirror"), {
  ssr: false,
  loading: () => <div className="h-full min-h-[420px] animate-pulse bg-surface-2" aria-hidden />,
});

function subscribeTheme(fn: () => void) {
  const obs = new MutationObserver(fn);
  obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return () => obs.disconnect();
}

export function CodeEditor({
  value,
  onChange,
  language,
  label,
  minHeight = "520px",
}: {
  value: string;
  onChange: (v: string) => void;
  language: "yaml" | "json";
  label: string;
  minHeight?: string;
}) {
  const theme = useSyncExternalStore(
    subscribeTheme,
    () => (document.documentElement.dataset.theme === "light" ? "light" : "dark"),
    () => "dark" as const,
  );
  const extensions = useMemo(
    () => [
      language === "yaml" ? yaml() : json(),
      EditorView.lineWrapping,
      EditorView.contentAttributes.of({ "aria-label": label }),
      EditorView.theme({
        "&": { backgroundColor: "var(--bg)", minHeight },
        ".cm-gutters": { backgroundColor: "var(--bg)", borderRight: "1px solid var(--border)", color: "var(--faint)" },
        ".cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--accent) 6%, transparent)" },
        ".cm-activeLineGutter": { backgroundColor: "transparent", color: "var(--muted)" },
        ".cm-content": { fontFamily: "var(--font-mono)", padding: "12px 0" },
        ".cm-scroller": { fontFamily: "var(--font-mono)", lineHeight: "1.6" },
      }),
    ],
    [language, label, minHeight],
  );
  return (
    <CodeMirror
      value={value}
      onChange={onChange}
      theme={theme}
      extensions={extensions}
      basicSetup={{ foldGutter: true, highlightActiveLine: true, autocompletion: false }}
      className="overflow-hidden"
    />
  );
}
