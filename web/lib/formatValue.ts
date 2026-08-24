// Tool input/output payloads are arbitrary JSON dicts (ToolResult.output in
// src/agentsys/tools/base.py) -- dumping them through JSON.stringify reads
// as a wall of braces and quotes in a chat feed. This renders the same data
// as plain indented "key: value" text instead, so it reads like a person
// wrote it rather than like a debugger.
export function humanizeValue(value: unknown, indent = 0): string {
  const pad = "  ".repeat(indent);
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);

  if (Array.isArray(value)) {
    if (value.length === 0) return "(empty)";
    return value
      .map((v) => {
        const rendered = humanizeValue(v, indent + 1);
        return rendered.includes("\n") ? `${pad}-\n${rendered}` : `${pad}- ${rendered}`;
      })
      .join("\n");
  }

  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) return "(empty)";
  return entries
    .map(([k, v]) => {
      const rendered = humanizeValue(v, indent + 1);
      return rendered.includes("\n") ? `${pad}${k}:\n${rendered}` : `${pad}${k}: ${rendered}`;
    })
    .join("\n");
}
