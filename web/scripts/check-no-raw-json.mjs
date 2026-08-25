// No raw JSON on screen.
//
// A tool result is an arbitrary dict (ToolResult.output in
// src/agentsys/tools/base.py). Rendering it with JSON.stringify puts braces,
// quotes and escapes in front of a person who wants to know what the agent
// did. lib/formatValue.ts's humanizeValue exists precisely so that never has
// to happen.
//
// The rule kept breaking by accident -- three separate components had drifted
// back to JSON.stringify, and the spill pointer had no render branch at all,
// so it dumped its own plumbing (including a `hint` written for the model)
// into the trace. A convention nothing checks is a convention that decays.
//
// Plain Node, no dependencies: this repo has no ESLint installed and this
// check is not worth a toolchain. Run with `npm run check:ui` (or `make ui-check`).

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = join(fileURLToPath(new URL("../", import.meta.url)));
const ROOTS = ["components", "app", "lib"];

// Serializing for the wire is fine -- a request body and localStorage are not
// the screen. Only rendering is banned.
const ALLOWED = new Set([
  "lib/api.ts",
  "lib/ragApi.ts",
  "lib/formatValue.ts",
  "components/feed/FeedFilterBar.tsx",
]);

function* sources(dir) {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry.startsWith(".")) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) yield* sources(full);
    else if (/\.tsx?$/.test(entry)) yield full;
  }
}

/** Comments are stripped before matching. Otherwise this flags the very
 *  comments that explain the rule ("humanizeValue, not JSON.stringify"),
 *  which it did on its first run -- a checker that punishes documenting its
 *  own convention teaches people to stop documenting it. */
function code(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, "") // block and JSX comments
    .replace(/^\s*\/\/.*$/gm, "");    // whole-line comments
}

const failures = [];

for (const root of ROOTS) {
  for (const file of sources(join(WEB, root))) {
    const rel = relative(WEB, file).split(sep).join("/");
    if (ALLOWED.has(rel)) continue;
    if (code(readFileSync(file, "utf8")).includes("JSON.stringify")) {
      failures.push(`${rel}: JSON.stringify in rendering code — use humanizeValue()`);
    }
  }
}

// The spill pointer is a known shape; without an explicit branch it falls
// through to the generic dump, which is how `hint` reached the UI.
const stepOutput = readFileSync(join(WEB, "components/ask/StepOutput.tsx"), "utf8");
if (!stepOutput.includes("_truncated")) {
  failures.push("components/ask/StepOutput.tsx: no render branch for a spilled result");
}
if (!stepOutput.includes("summary")) {
  failures.push("components/ask/StepOutput.tsx: should lead with the digest, not the preview");
}

if (failures.length) {
  console.error("\nRaw JSON would reach the UI:\n");
  for (const f of failures) console.error(`  ${f}`);
  console.error("");
  process.exit(1);
}

console.log("ui check: no raw JSON in rendering code");
