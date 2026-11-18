import type { NextConfig } from "next";

// Static export: the built panel in `out/` is served by the core Python server at `/`.
// trailingSlash makes every route a folder with index.html, so any static server works.
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  // next dev would otherwise write an AGENTS.md that uses long dash characters (repo rule: none).
  agentRules: false,
  turbopack: {
    root: __dirname,
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;


