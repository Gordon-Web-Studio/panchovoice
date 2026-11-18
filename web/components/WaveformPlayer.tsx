"use client";
import { useCallback, useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";
import { Loader2, Pause, Play } from "lucide-react";
import { api } from "@/lib/api";
import { clock } from "@/lib/format";
import type { Turn } from "@/lib/types";

export interface PlayerHandle {
  seek: (ms: number, play?: boolean) => void;
}

interface Peaks {
  left: Float32Array;
  right: Float32Array;
  stereo: boolean;
  durationMs: number;
}

const BUCKETS = 1600;

function computePeaks(buffer: AudioBuffer): Peaks {
  const l = buffer.getChannelData(0);
  const r = buffer.numberOfChannels > 1 ? buffer.getChannelData(1) : l;
  const size = Math.max(1, Math.floor(l.length / BUCKETS));
  const left = new Float32Array(BUCKETS);
  const right = new Float32Array(BUCKETS);
  for (let b = 0; b < BUCKETS; b++) {
    let ml = 0;
    let mr = 0;
    const start = b * size;
    const end = Math.min(l.length, start + size);
    for (let i = start; i < end; i++) {
      const a = Math.abs(l[i]);
      const c = Math.abs(r[i]);
      if (a > ml) ml = a;
      if (c > mr) mr = c;
    }
    left[b] = ml;
    right[b] = mr;
  }
  // normalize each lane so quiet recordings still show shape
  const norm = (arr: Float32Array) => {
    let max = 0;
    for (const v of arr) if (v > max) max = v;
    if (max > 0) for (let i = 0; i < arr.length; i++) arr[i] = arr[i] / max;
  };
  norm(left);
  norm(right);
  return { left, right, stereo: buffer.numberOfChannels > 1, durationMs: buffer.duration * 1000 };
}

function cssVar(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function WaveformPlayer({ runId, turns, durationMs, ref }: { runId: string; turns: Turn[]; durationMs: number; ref?: Ref<PlayerHandle> }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const [src, setSrc] = useState<string | null>(null);
  const [peaks, setPeaks] = useState<Peaks | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [pos, setPos] = useState(0);
  const total = peaks?.durationMs || durationMs || 1;

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const url = await api.audioUrl(runId, "stereo");
        const res = await fetch(url);
        if (!res.ok) throw new Error(`Audio not found (${res.status})`);
        const data = await res.arrayBuffer();
        const ctx = new AudioContext();
        const buffer = await ctx.decodeAudioData(data);
        ctx.close();
        if (!alive) return;
        setSrc(url);
        setPeaks(computePeaks(buffer));
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, [runId]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap || !peaks) return;
    const dpr = window.devicePixelRatio || 1;
    const w = wrap.clientWidth;
    const h = 168;
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    const g = canvas.getContext("2d");
    if (!g) return;
    g.scale(dpr, dpr);
    g.clearRect(0, 0, w, h);
    const caller = cssVar("--caller");
    const agent = cssVar("--agent");
    const border = cssVar("--border");
    const faint = cssVar("--surface-2");
    const laneH = h / 2;
    const progressX = (pos / total) * w;

    // turn regions as faint bands
    g.fillStyle = faint;
    for (const t of turns) {
      g.fillRect((t.caller_start_ms / total) * w, 0, ((t.caller_end_ms - t.caller_start_ms) / total) * w, laneH - 1);
      g.fillRect((t.agent_start_ms / total) * w, laneH + 1, ((t.agent_end_ms - t.agent_start_ms) / total) * w, laneH - 1);
    }
    g.fillStyle = border;
    g.fillRect(0, laneH, w, 1);

    const bars = Math.max(60, Math.floor(w / 3));
    const barW = Math.max(1, w / bars - 1);
    const lane = (data: Float32Array, color: string, top: number) => {
      const mid = top + laneH / 2;
      for (let i = 0; i < bars; i++) {
        const from = Math.floor((i / bars) * data.length);
        const to = Math.max(from + 1, Math.floor(((i + 1) / bars) * data.length));
        let v = 0;
        for (let k = from; k < to; k++) if (data[k] > v) v = data[k];
        const x = (i / bars) * w;
        const bh = Math.max(1.5, v * (laneH / 2 - 8) * 2);
        g.globalAlpha = x <= progressX ? 1 : 0.42;
        g.fillStyle = color;
        g.fillRect(x, mid - bh / 2, barW, bh);
      }
      g.globalAlpha = 1;
    };
    lane(peaks.left, caller, 0);
    lane(peaks.right, agent, laneH);

    // interrupted markers
    g.fillStyle = cssVar("--warn");
    for (const t of turns) {
      if (t.interrupted) {
        const x = (t.agent_end_ms / total) * w;
        g.fillRect(x - 1, laneH + 4, 2, laneH - 8);
      }
    }
    // playhead
    g.fillStyle = cssVar("--fg");
    g.fillRect(progressX - 0.75, 0, 1.5, h);
  }, [peaks, pos, total, turns]);

  useEffect(() => {
    draw();
  }, [draw]);

  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const obs = new ResizeObserver(() => draw());
    obs.observe(wrap);
    const mo = new MutationObserver(() => draw());
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => {
      obs.disconnect();
      mo.disconnect();
    };
  }, [draw]);

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    const loop = () => {
      const a = audioRef.current;
      if (a) setPos(a.currentTime * 1000);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [playing]);

  const seek = useCallback(
    (msValue: number, play = false) => {
      const a = audioRef.current;
      const clamped = Math.max(0, Math.min(total, msValue));
      setPos(clamped);
      if (a) {
        a.currentTime = clamped / 1000;
        if (play) a.play().catch(() => undefined);
      }
    },
    [total],
  );

  useImperativeHandle(ref, () => ({ seek }), [seek]);

  function toggle() {
    const a = audioRef.current;
    if (!a) return;
    if (a.paused) a.play().catch(() => undefined);
    else a.pause();
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-4">
        <button
          type="button"
          onClick={toggle}
          disabled={!src}
          className="inline-flex h-10 w-10 items-center justify-center rounded-full bg-accent text-accent-fg shadow-sm transition hover:brightness-110 disabled:opacity-50"
          aria-label={playing ? "Pause recording" : "Play recording"}
        >
          {!src && !error ? <Loader2 size={18} className="animate-spin" /> : playing ? <Pause size={18} /> : <Play size={18} className="ml-0.5" />}
        </button>
        <div className="tabular font-mono text-sm text-fg">
          {clock(pos)} <span className="text-faint">/ {clock(total)}</span>
        </div>
        <div className="ml-auto flex items-center gap-4 text-xs text-muted">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm bg-caller" aria-hidden /> Caller (left)
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm bg-agent" aria-hidden /> Agent (right)
          </span>
          {turns.some((t) => t.interrupted) && (
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-0.5 bg-warn" aria-hidden /> Barge-in stop
            </span>
          )}
        </div>
      </div>
      <div
        ref={wrapRef}
        role="slider"
        tabIndex={0}
        aria-label="Recording position. Use arrow keys to seek."
        aria-valuemin={0}
        aria-valuemax={Math.round(total / 1000)}
        aria-valuenow={Math.round(pos / 1000)}
        aria-valuetext={`${clock(pos)} of ${clock(total)}`}
        className="relative cursor-pointer overflow-hidden rounded-lg border border-border bg-bg"
        onClick={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          seek(((e.clientX - rect.left) / rect.width) * total);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowRight") seek(pos + 1000);
          else if (e.key === "ArrowLeft") seek(pos - 1000);
          else if (e.key === " " || e.key === "Enter") {
            e.preventDefault();
            toggle();
          } else return;
          e.preventDefault();
        }}
      >
        <canvas ref={canvasRef} className="block" />
        {!peaks && (
          <div className="flex h-[168px] items-center justify-center text-sm text-muted">
            {error ? `Could not load the recording: ${error}` : "Loading recording..."}
          </div>
        )}
        <span className="pointer-events-none absolute left-1.5 top-1.5 rounded bg-bg/85 px-1 text-[10px] font-medium uppercase tracking-wide text-caller">Caller</span>
        <span className="pointer-events-none absolute bottom-1.5 left-1.5 rounded bg-bg/85 px-1 text-[10px] font-medium uppercase tracking-wide text-agent">Agent</span>
      </div>
      {peaks && !peaks.stereo && <p className="mt-2 text-xs text-warn">This file is mono. Both lanes show the same channel.</p>}
      {src && (
        <audio
          ref={audioRef}
          src={src}
          preload="auto"
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => {
            setPlaying(false);
            setPos(total);
          }}
          className="hidden"
        />
      )}
    </div>
  );
}
