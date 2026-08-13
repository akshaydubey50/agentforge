import type { TaskDetailOut, TraceSpanOut } from "@/lib/api";
import { SUBTASK_STATUS_META, TASK_STATUS_META } from "@/lib/agentStatus";
import { cn } from "@/lib/utils";

function elapsedLabel(createdAt: string, updatedAt: string, running: boolean) {
  const start = new Date(createdAt).getTime();
  const end = running ? Date.now() : new Date(updatedAt).getTime();
  const seconds = Math.max(0, (end - start) / 1000);
  return seconds < 60 ? `${seconds.toFixed(1)}s` : `${Math.round(seconds / 60)}m`;
}

export function TaskContextPanel({ task, spans }: { task: TaskDetailOut; spans: TraceSpanOut[] }) {
  const meta = TASK_STATUS_META[task.status];
  const sketchSpan = spans.find((s) => s.span_type === "sketch");
  const confidence = (sketchSpan?.output as { confidence?: number })?.confidence;
  const toolCalls = spans.filter((s) => s.span_type === "tool_call").length;

  return (
    <div className="flex w-[300px] flex-none flex-col overflow-y-auto border-l border-border bg-rail">
      <div className="border-b border-border px-4.5 py-3.5 text-[11px] uppercase tracking-wide text-text-faint">
        Task context
      </div>

      <Section label="Status">
        <Row k="state">
          <span className={cn("font-semibold", meta.text)}>{meta.label}</span>
        </Row>
        <Row k="elapsed">{elapsedLabel(task.created_at, task.updated_at, task.status === "running" || task.status === "pending")}</Row>
        {confidence !== undefined && <Row k="plan confidence">{confidence} / 5</Row>}
      </Section>

      <Section label="Subtasks">
        {task.subtasks.length === 0 && <div className="text-[12px] text-text-faint">none yet</div>}
        {task.subtasks.map((s) => {
          const sMeta = SUBTASK_STATUS_META[s.status];
          return (
            <div key={s.id} className="mb-1.5 flex items-center gap-2 text-[12px] last:mb-0">
              <span className={cn("h-2 w-2 flex-none rounded-sm", sMeta.dot)} />
              <span className="mono truncate text-text">
                #{s.position} {s.assigned_tool ?? "reasoning"}
              </span>
              <span className="ml-auto flex-none text-[11px] text-text-faint">{sMeta.label}</span>
            </div>
          );
        })}
      </Section>

      <Section label="Run totals">
        <Row k="trace spans">{spans.length}</Row>
        <Row k="tool calls">{toolCalls}</Row>
      </Section>
    </div>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="border-b border-border px-4.5 py-3.5">
      <div className="mb-2 text-[10.5px] uppercase tracking-wide text-text-faint">{label}</div>
      {children}
    </div>
  );
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="mb-2 flex justify-between text-[12px] last:mb-0">
      <span className="text-text-muted">{k}</span>
      <span className="mono text-text">{children}</span>
    </div>
  );
}
