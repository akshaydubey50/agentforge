"use client";

// The agent, reachable from every page.
//
// Before this, starting a task meant navigating to /ask and leaving whatever
// you were inspecting. That is backwards for a system whose whole value is
// watching what it does: the most common reason to want the agent is
// something you just noticed on Analytics, Memory or System.
//
// It is live rather than polled -- steps arrive over the task's SSE stream
// (see lib/useTaskEvents.ts) -- but every event only triggers a revalidation
// of the real record, so a dropped stream degrades to normal polling instead
// of a stuck panel.

import { useCallback, useMemo, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { api, type TaskDetailOut } from "@/lib/api";
import { useTaskEvents, type TaskEvent } from "@/lib/useTaskEvents";
import { isActiveTaskStatus } from "@/lib/agentStatus";
import { cn } from "@/lib/utils";

interface LiveStep {
  id: string;
  spanType: string;
  name: string;
  status: "running" | "ok" | "error";
  durationMs?: number;
}

/** span_start opens a row, span_end closes it. Keyed by span_id so a retry
 *  of the same step is its own row rather than overwriting the first
 *  attempt -- the retry loop is exactly what you want to see. */
function toSteps(events: TaskEvent[]): LiveStep[] {
  const rows = new Map<string, LiveStep>();
  for (const event of events) {
    if (!event.span_id) continue;
    if (event.kind === "span_start") {
      rows.set(event.span_id, {
        id: event.span_id,
        spanType: event.span_type ?? "step",
        name: event.name ?? event.span_type ?? "step",
        status: "running",
      });
    } else if (event.kind === "span_end") {
      const existing = rows.get(event.span_id);
      rows.set(event.span_id, {
        id: event.span_id,
        spanType: event.span_type ?? existing?.spanType ?? "step",
        name: event.name ?? existing?.name ?? "step",
        status: event.status === "ok" ? "ok" : "error",
        durationMs: event.duration_ms,
      });
    }
  }
  return [...rows.values()];
}

const STATUS_DOT: Record<LiveStep["status"], string> = {
  running: "bg-status-running animate-pulse",
  ok: "bg-status-completed",
  error: "bg-status-failed",
};

export function AskDock() {
  const [open, setOpen] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data: task, mutate } = useSWR<TaskDetailOut | null>(
    taskId ? ["dock-task", taskId] : null,
    () => api.getTask(taskId!),
    {
      // The stream drives freshness; this interval is the safety net for a
      // stream that never connected (Redis down, a proxy eating SSE).
      refreshInterval: (data) => (data && isActiveTaskStatus(data.status) ? 4000 : 0),
    }
  );

  const onEvent = useCallback(() => {
    void mutate();
  }, [mutate]);

  const { events, state } = useTaskEvents(taskId, { onEvent, enabled: open || !!taskId });
  const steps = useMemo(() => toSteps(events), [events]);

  const running = task ? isActiveTaskStatus(task.status) : false;

  const submit = async () => {
    const text = draft.trim();
    if (!text || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const created = await api.createTask(text);
      setTaskId(created.id);
      setDraft("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not start the task");
    } finally {
      setSubmitting(false);
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-5 right-5 z-40 flex items-center gap-2 rounded-full border border-border-strong bg-surface px-4 py-2.5 text-[13px] font-medium text-text shadow-[0_6px_20px_rgba(0,0,0,0.55)] transition-colors hover:border-brand"
      >
        {running && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-status-running" />}
        Ask
      </button>
    );
  }

  return (
    <div className="fixed bottom-5 right-5 z-40 flex max-h-[min(560px,80vh)] w-[380px] flex-col overflow-hidden rounded-[var(--rl)] border border-border-strong bg-surface shadow-[0_24px_60px_rgba(0,0,0,0.7)]">
      <div className="flex flex-none items-center justify-between border-b border-border px-4 py-2.5">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-semibold text-text">Ask</span>
          {taskId && (
            <span
              className={cn(
                "text-[10.5px] uppercase tracking-[0.08em]",
                state === "open" ? "text-status-completed" : "text-text-faint"
              )}
              title={
                state === "open"
                  ? "Live event stream connected"
                  : "Stream not connected — falling back to polling"
              }
            >
              {state === "open" ? "live" : state}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          {taskId && (
            <Link
              href={`/tasks/${taskId}`}
              className="rounded-[6px] px-2 py-1 text-[11.5px] text-text-muted transition-colors hover:text-brand"
            >
              Open →
            </Link>
          )}
          <button
            onClick={() => setOpen(false)}
            aria-label="Close"
            className="rounded-[6px] px-2 py-1 text-[13px] text-text-faint transition-colors hover:text-text"
          >
            ×
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {!taskId && (
          <p className="text-[12.5px] leading-relaxed text-text-faint">
            Start a task from anywhere. Steps appear here as the agent takes them.
          </p>
        )}

        {task && (
          <div className="mb-3">
            <div className="text-[12.5px] leading-relaxed text-text">{task.request_text}</div>
            <div className="mt-1 text-[11px] uppercase tracking-[0.07em] text-text-faint">{task.status}</div>
          </div>
        )}

        {steps.length > 0 && (
          <ol className="space-y-1.5 border-l border-border pl-3">
            {steps.map((step) => (
              <li key={step.id} className="flex items-baseline gap-2 text-[12px]">
                <span className={cn("h-1.5 w-1.5 flex-none translate-y-[-1px] rounded-full", STATUS_DOT[step.status])} />
                <span className="min-w-0 flex-1 truncate text-text-muted">{step.name}</span>
                {step.durationMs !== undefined && (
                  <span className="flex-none tabular-nums text-[10.5px] text-text-faint">
                    {step.durationMs < 1000 ? `${step.durationMs}ms` : `${(step.durationMs / 1000).toFixed(1)}s`}
                  </span>
                )}
              </li>
            ))}
          </ol>
        )}

        {task?.final_output && (
          <div className="mt-3 rounded-[var(--rs)] border border-border bg-surface-2 px-3 py-2.5 text-[12.5px] leading-relaxed text-text">
            {task.final_output}
          </div>
        )}

        {task?.status === "awaiting_approval" && (
          <Link
            href="/approvals"
            className="mt-3 block rounded-[var(--rs)] border border-role-human/40 bg-role-human/10 px-3 py-2.5 text-[12.5px] text-role-human"
          >
            Paused — needs your decision in Approvals →
          </Link>
        )}

        {error && <div className="mt-3 text-[12px] text-bad">{error}</div>}
      </div>

      <div className="flex-none border-t border-border p-2.5">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void submit();
            }
          }}
          rows={2}
          placeholder={taskId ? "Start another task…" : "What should the agent do?"}
          className="w-full resize-none rounded-[var(--rs)] border border-border bg-surface-2 px-3 py-2 text-[12.5px] text-text outline-none placeholder:text-text-faint focus:border-brand"
        />
        <div className="mt-2 flex items-center justify-between">
          <span className="text-[10.5px] text-text-faint">Enter to send</span>
          <button
            onClick={() => void submit()}
            disabled={!draft.trim() || submitting}
            className="rounded-[var(--rs)] bg-brand px-3 py-1.5 text-[12px] font-medium text-brand-foreground transition-opacity disabled:opacity-40"
          >
            {submitting ? "Starting…" : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
}
