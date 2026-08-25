import { Collapsible } from "@/components/ui/Collapsible";
import { humanizeValue } from "@/lib/formatValue";
import { Markdown } from "@/components/ui/Markdown";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";

/** Renders a subtask's output the way the shape actually calls for --
 * never a raw JSON dump, never raw markdown left unparsed. A subtask's
 * `output` string is one of: a tool's JSON-serialized result (shape varies
 * by tool -- see the branches below, which mirror tools/*.py's actual
 * output dicts), a plain "Tool call failed: ..." error string, or free-text
 * from the model itself (reasoning-only steps, a human's take_over answer)
 * -- the last two are markdown from the model's point of view, not data. */
export function StepOutput({ output }: { output: string }) {
  if (!output) return null;

  if (output.startsWith("Tool call failed:") || output.startsWith("FAILED:")) {
    return <p className="text-[13px] italic text-bad">{output}</p>;
  }

  let data: unknown;
  try {
    data = JSON.parse(output);
  } catch {
    // Not JSON -- this is model-authored text (reasoning, a synthesized
    // answer, a human's typed override). Render it as markdown, same as
    // the final answer.
    return <Markdown>{output}</Markdown>;
  }

  if (data === null || typeof data !== "object") {
    return <p className="text-[13.5px] text-text">{String(data)}</p>;
  }

  if (Array.isArray(data)) {
    return <GenericList items={data} />;
  }

  const obj = data as Record<string, unknown>;

  // A spilled oversized result (artifacts.spill): {_truncated, total_chars,
  // summary, preview, full_output_path, hint}. Without this branch it fell
  // through to GenericObject and dumped the plumbing -- including `hint`,
  // which is an instruction written for the MODEL and reads as nonsense to a
  // person ("read it with file_io using..."). The digest is the content here;
  // everything else is machinery and belongs behind a disclosure.
  if (obj._truncated === true) {
    const summary = typeof obj.summary === "string" ? obj.summary.trim() : "";
    const preview = typeof obj.preview === "string" ? obj.preview : "";
    const total = typeof obj.total_chars === "number" ? obj.total_chars : null;
    return (
      <div className="space-y-2">
        {summary ? (
          <Markdown>{summary}</Markdown>
        ) : (
          <pre className="mono overflow-x-auto rounded-[var(--rs)] border border-border bg-surface-2 p-2.5 text-[12.5px] whitespace-pre-wrap text-text">
            {asReadableText(preview)}
          </pre>
        )}
        <p className="text-[11.5px] text-text-faint">
          {summary ? "Summarized from " : "First 1,200 characters of "}
          {total ? `a ${total.toLocaleString()}-character result` : "a long result"}
          {" — the full text is kept in this task's files."}
        </p>
        {summary && preview && (
          <Collapsible label="Show the raw opening">
            <pre className="mono overflow-x-auto rounded-[var(--rs)] border border-border bg-surface-2 p-2.5 text-[12px] whitespace-pre-wrap text-text-muted">
              {asReadableText(preview)}
            </pre>
          </Collapsible>
        )}
      </div>
    );
  }

  // code_execution: {stdout, stderr, exit_code}
  if ("stdout" in obj || "stderr" in obj) {
    const stdout = typeof obj.stdout === "string" ? obj.stdout.trim() : "";
    const stderr = typeof obj.stderr === "string" ? obj.stderr.trim() : "";
    const exitCode = obj.exit_code;
    return (
      <div className="space-y-1.5">
        {stdout && (
          <pre className="mono overflow-x-auto rounded-[var(--rs)] border border-border bg-surface-2 p-2.5 text-[12.5px] whitespace-pre-wrap text-text">
            {stdout}
          </pre>
        )}
        {stderr && (
          <pre className="mono overflow-x-auto rounded-[var(--rs)] border border-bad/30 bg-bad-wash p-2.5 text-[12.5px] whitespace-pre-wrap text-bad">
            {stderr}
          </pre>
        )}
        {!stdout && !stderr && <p className="text-[13px] text-text-faint">No output produced.</p>}
        {typeof exitCode === "number" && exitCode !== 0 && (
          <p className="text-[11.5px] text-text-faint">Exit code {exitCode}</p>
        )}
      </div>
    );
  }

  // db_query: {columns, rows}
  if ("columns" in obj && "rows" in obj) {
    const columns = Array.isArray(obj.columns) ? (obj.columns as unknown[]) : [];
    const rows = Array.isArray(obj.rows) ? (obj.rows as unknown[][]) : [];
    if (rows.length === 0) {
      return <p className="text-[13px] text-text-faint">Query returned no rows.</p>;
    }
    return (
      <Table>
        <TableHeader>
          <TableRow>
            {columns.map((c, i) => (
              <TableHead key={i}>{String(c)}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, ri) => (
            <TableRow key={ri}>
              {row.map((cell, ci) => (
                <TableCell key={ci} className="tabular">
                  {String(cell)}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    );
  }

  // knowledge_search / delegate_subagent: {answer, sources?} / {answer, steps?}
  if (typeof obj.answer === "string") {
    const sources = Array.isArray(obj.sources) ? (obj.sources as unknown[]) : [];
    return (
      <div>
        <Markdown>{obj.answer}</Markdown>
        {sources.length > 0 && (
          <p className="mt-1 text-[11.5px] text-text-faint">Sources: {sources.map(String).join(", ")}</p>
        )}
        {typeof obj.warning === "string" && (
          <p className="mt-1 text-[11.5px] text-warn">{obj.warning}</p>
        )}
      </div>
    );
  }

  // web_search: {results: [{title, url, snippet}]}
  if (Array.isArray(obj.results)) {
    const results = obj.results as Array<Record<string, unknown>>;
    if (results.length === 0) return <p className="text-[13px] text-text-faint">No results found.</p>;
    return (
      <ul className="space-y-1.5">
        {results.map((r, i) => (
          <li key={i} className="text-[13px]">
            {typeof r.url === "string" ? (
              <a href={r.url} target="_blank" rel="noopener noreferrer" className="font-medium text-brand underline underline-offset-2">
                {String(r.title ?? r.url)}
              </a>
            ) : (
              <span className="font-medium text-text">{String(r.title ?? "")}</span>
            )}
            {typeof r.snippet === "string" && r.snippet && (
              <p className="mt-0.5 text-text-muted">{r.snippet}</p>
            )}
          </li>
        ))}
      </ul>
    );
  }

  // file_io read: {content}. Reading back a spilled artifact returns the
  // harness's own JSON, so it is decoded rather than shown as braces.
  if (typeof obj.content === "string") {
    return (
      <pre className="mono overflow-x-auto rounded-[var(--rs)] border border-border bg-surface-2 p-2.5 text-[12.5px] whitespace-pre-wrap text-text">
        {asReadableText(obj.content)}
      </pre>
    );
  }

  // Anything else (MCP tool results, file_io write/list, etc.) -- a clean
  // label: value list beats a raw JSON dump even when we don't know the
  // tool's specific shape ahead of time.
  return <GenericObject obj={obj} />;
}

function GenericObject({ obj }: { obj: Record<string, unknown> }) {
  const entries = Object.entries(obj);
  if (entries.length === 0) return null;
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-2.5 gap-y-1 text-[13px]">
      {entries.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="font-medium text-text-faint">{key}</dt>
          <dd className="m-0 text-text">{formatScalar(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function GenericList({ items }: { items: unknown[] }) {
  if (items.length === 0) return <p className="text-[13px] text-text-faint">(empty)</p>;
  return (
    <ul className="ml-4.5 list-disc space-y-1 text-[13px] text-text">
      {items.map((item, i) => (
        <li key={i}>{typeof item === "object" && item !== null ? <GenericObject obj={item as Record<string, unknown>} /> : formatScalar(item)}</li>
      ))}
    </ul>
  );
}

function formatScalar(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return value.map(formatScalar).join(", ");
  // humanizeValue rather than JSON.stringify: this is the fallback for tool
  // shapes we don't have a branch for (MCP servers contribute arbitrary
  // ones), so it is exactly where a raw dump would otherwise leak through.
  if (typeof value === "object") return humanizeValue(value);
  return String(value);
}

/** A string that is itself serialized JSON, rendered as text.
 *
 * Some payloads arrive double-encoded: a tool returns a dict, the harness
 * json.dumps it, and that string becomes the `preview` or a file's contents.
 * Showing it raw puts braces and escaped quotes on screen for no reason, so
 * it is parsed back and humanized. Anything that isn't JSON is returned
 * untouched -- ordinary prose must not be mangled by this. */
function asReadableText(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return raw;
  try {
    return humanizeValue(JSON.parse(trimmed));
  } catch {
    return raw;
  }
}
