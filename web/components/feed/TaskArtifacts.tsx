"use client";

// The files a task actually produced, openable.
//
// The feed already says "wrote report.txt". Being told an artifact exists and
// then not being able to look at it is the gap this closes -- for a system
// whose whole pitch is that every decision is inspectable, the deliverable
// itself was the one thing you couldn't inspect.
//
// Deliverables are listed first and spillover is folded away, because they
// are different things to a reader: one is the point of the task, the other
// is the harness keeping the prompt small (see artifacts.py). A two-line
// report should not be buried under a dozen 30KB JSON dumps.

import { useState } from "react";
import useSWR from "swr";
import { api, type TaskArtifact } from "@/lib/api";
import { cn } from "@/lib/utils";

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function FileRow({
  file,
  taskId,
  open,
  onToggle,
}: {
  file: TaskArtifact;
  taskId: string;
  open: boolean;
  onToggle: () => void;
}) {
  // Content is fetched only once a row is opened -- a task can hold many
  // megabytes of spillover and none of it is worth loading to render a list.
  const { data, isLoading } = useSWR(
    open ? ["artifact", taskId, file.path] : null,
    () => api.readArtifact(taskId, file.path)
  );

  return (
    <li className="border-b border-border last:border-b-0">
      <button
        onClick={onToggle}
        className="flex w-full items-baseline gap-2 py-1.5 text-left transition-colors hover:text-text"
      >
        <span className={cn("flex-none text-[10px]", open ? "text-brand" : "text-text-faint")}>
          {open ? "▾" : "▸"}
        </span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-text-muted">
          {file.path}
        </span>
        <span className="flex-none tabular-nums text-[10.5px] text-text-faint">
          {formatBytes(file.bytes)}
        </span>
      </button>

      {open && (
        <div className="pb-2 pl-4">
          {isLoading && <div className="text-[11px] text-text-faint">Loading…</div>}
          {data?.binary && (
            <div className="text-[11px] text-text-faint">
              Binary file — not shown. {formatBytes(data.bytes)} on disk.
            </div>
          )}
          {data && !data.binary && (
            <>
              <pre className="max-h-[260px] overflow-auto whitespace-pre-wrap break-words rounded-[var(--rs)] border border-border bg-surface-2 px-2.5 py-2 font-mono text-[11px] leading-relaxed text-text">
                {data.content}
              </pre>
              {data.truncated && (
                <div className="mt-1 text-[10.5px] text-text-faint">
                  Showing the first 200,000 characters of {formatBytes(data.bytes)}.
                </div>
              )}
            </>
          )}
        </div>
      )}
    </li>
  );
}

export function TaskArtifacts({ taskId }: { taskId: string }) {
  const [openPath, setOpenPath] = useState<string | null>(null);
  const [showSpillover, setShowSpillover] = useState(false);

  const { data } = useSWR(["artifacts", taskId], () => api.listArtifacts(taskId), {
    refreshInterval: 10000,
  });

  // A task that never touched the filesystem is the common case, and an
  // empty panel is noise -- render nothing rather than an empty state.
  if (!data || data.files.length === 0) return null;

  const deliverables = data.files.filter((f) => f.kind === "deliverable");
  const spillover = data.files.filter((f) => f.kind === "spillover");

  const toggle = (path: string) => setOpenPath((current) => (current === path ? null : path));

  return (
    <section className="border-t border-border px-4 py-3.5">
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-[11px] uppercase tracking-wide text-text-faint">Files</span>
        <span className="text-[10.5px] text-text-faint">{formatBytes(data.total_bytes)}</span>
      </div>

      {deliverables.length > 0 && (
        <ul className="mb-1">
          {deliverables.map((file) => (
            <FileRow
              key={file.path}
              file={file}
              taskId={taskId}
              open={openPath === file.path}
              onToggle={() => toggle(file.path)}
            />
          ))}
        </ul>
      )}

      {spillover.length > 0 && (
        <>
          <button
            onClick={() => setShowSpillover((v) => !v)}
            className="mt-1 text-[11px] text-text-faint transition-colors hover:text-text-muted"
          >
            {showSpillover ? "▾" : "▸"} {spillover.length} oversized tool result
            {spillover.length === 1 ? "" : "s"} parked by the harness
          </button>
          {showSpillover && (
            <ul className="mt-1">
              {spillover.map((file) => (
                <FileRow
                  key={file.path}
                  file={file}
                  taskId={taskId}
                  open={openPath === file.path}
                  onToggle={() => toggle(file.path)}
                />
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
