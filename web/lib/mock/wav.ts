// Builds a small stereo WAV for mock mode: left = caller, right = agent.
// The sound is a soft, speech-like hum shaped by syllables, placed at the turn times.
import type { RunResult } from "../types";

const RATE = 8000; // lower than the real 16 kHz to keep the mock file small

function writeString(view: DataView, offset: number, text: string) {
  for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
}

function fillSpeech(channel: Float32Array, startMs: number, endMs: number, pitch: number, seed: number) {
  const start = Math.max(0, Math.floor((startMs / 1000) * RATE));
  const end = Math.min(channel.length, Math.floor((endMs / 1000) * RATE));
  let s = seed;
  const rand = () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
  // syllable plan: 140 to 260 ms each, with short gaps between words
  let i = start;
  while (i < end) {
    const syl = Math.floor(((140 + rand() * 120) / 1000) * RATE);
    const gap = rand() < 0.28 ? Math.floor(((60 + rand() * 120) / 1000) * RATE) : Math.floor(0.015 * RATE);
    const amp = 0.18 + rand() * 0.22;
    const f = pitch * (0.9 + rand() * 0.25);
    for (let k = 0; k < syl && i + k < end; k++) {
      const env = Math.sin((Math.PI * k) / syl);
      const tt = (i + k) / RATE;
      const v =
        Math.sin(2 * Math.PI * f * tt) * 0.6 +
        Math.sin(2 * Math.PI * f * 2 * tt) * 0.25 +
        Math.sin(2 * Math.PI * f * 3 * tt) * 0.12 +
        (rand() - 0.5) * 0.08;
      channel[i + k] = v * env * amp;
    }
    i += syl + gap;
  }
}

export function buildStereoWav(run: RunResult): Blob {
  const seconds = Math.max(2, run.duration_ms / 1000);
  const frames = Math.floor(seconds * RATE);
  const left = new Float32Array(frames);
  const right = new Float32Array(frames);
  run.turns.forEach((t, i) => {
    fillSpeech(left, t.caller_start_ms, t.caller_end_ms, 205, 1000 + i * 7);
    fillSpeech(right, t.agent_start_ms, t.agent_end_ms, 138, 5000 + i * 13);
  });

  const dataBytes = frames * 2 * 2;
  const buffer = new ArrayBuffer(44 + dataBytes);
  const view = new DataView(buffer);
  writeString(view, 0, "RIFF");
  view.setUint32(4, 36 + dataBytes, true);
  writeString(view, 8, "WAVE");
  writeString(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 2, true); // stereo
  view.setUint32(24, RATE, true);
  view.setUint32(28, RATE * 4, true);
  view.setUint16(32, 4, true);
  view.setUint16(34, 16, true);
  writeString(view, 36, "data");
  view.setUint32(40, dataBytes, true);
  let o = 44;
  for (let i = 0; i < frames; i++) {
    view.setInt16(o, Math.max(-1, Math.min(1, left[i])) * 0x7fff, true);
    view.setInt16(o + 2, Math.max(-1, Math.min(1, right[i])) * 0x7fff, true);
    o += 4;
  }
  return new Blob([buffer], { type: "audio/wav" });
}
