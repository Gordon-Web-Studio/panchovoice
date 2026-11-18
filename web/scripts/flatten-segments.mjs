// Post build step for the static export.
// Next 16 writes segment prefetch files as nested folders (out/runs/view/__next.runs/view/__PAGE__.txt)
// but the client asks for a dotted file name (out/runs/view/__next.runs.view.__PAGE__.txt).
// A plain static server then answers 404 for every prefetch. This copies each nested file to the dotted name.
import fs from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..", "out");
let copied = 0;

function filesUnder(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    return e.isDirectory() ? filesUnder(p) : [p];
  });
}

function walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (!e.isDirectory()) continue;
    const p = path.join(dir, e.name);
    if (e.name.startsWith("__next.")) {
      for (const file of filesUnder(p)) {
        const rel = path.relative(p, file).split(path.sep);
        const target = path.join(dir, [e.name, ...rel].join("."));
        if (!fs.existsSync(target)) {
          fs.copyFileSync(file, target);
          copied++;
        }
      }
    } else {
      walk(p);
    }
  }
}

if (fs.existsSync(root)) {
  walk(root);
  console.log(`flatten-segments: ${copied} prefetch files copied`);
}
