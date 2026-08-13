"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { api, type EscalationOut } from "@/lib/api";
import { classifyEscalation } from "@/lib/escalationTag";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

function ageLabel(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const totalSec = Math.max(0, Math.round(ms / 1000));
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return m > 0 ? `waiting ${m}m ${s.toString().padStart(2, "0")}s` : `waiting ${s}s`;
}

export function ApprovalCard({
  escalation,
  onDecide,
  focus,
}: {
  escalation: EscalationOut;
  onDecide: (decision: "approve" | "reject" | "take_over", overrideOutput?: string) => Promise<void>;
  focus?: boolean;
}) {
  const [takingOver, setTakingOver] = useState(false);
  const [overrideOutput, setOverrideOutput] = useState("");
  const [busy, setBusy] = useState(false);

  const { data: task } = useSWR(["task", escalation.task_id], () => api.getTask(escalation.task_id));
  const subtask = escalation.subtask_id ? task?.subtasks.find((s) => s.id === escalation.subtask_id) : undefined;
  const { tag, level } = classifyEscalation(escalation.reason, escalation.subtask_id);

  const decide = async (decision: "approve" | "reject" | "take_over", output?: string) => {
    setBusy(true);
    try {
      await onDecide(decision, output);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={cn("mb-4 overflow-hidden rounded-[var(--rm)] border border-border bg-surface", focus && "border-role-human")}>
      <div className="flex items-start justify-between border-b border-border px-5 py-4">
        <div>
          <Link href={`/tasks/${escalation.task_id}`} className="text-[14px] font-semibold text-text hover:underline">
            {task?.request_text ?? "…"}
          </Link>
          <div className="mono mt-0.5 text-[11px] text-text-faint">
            task_{escalation.task_id.slice(0, 8)}
            {subtask ? ` · subtask #${subtask.position} · ${subtask.assigned_tool ?? "reasoning"}` : " · plan-level"}
          </div>
        </div>
        <div className="mono flex-none text-[11.5px] text-text-muted">{ageLabel(escalation.created_at)}</div>
      </div>

      <div className="px-5 py-4">
        <div className="mb-1.5 text-[10.5px] uppercase tracking-wide text-text-faint">Why it escalated</div>
        <div className="mb-3.5 rounded-lg bg-canvas px-3.5 py-3 text-[13px] leading-relaxed text-text">
          <span
            className={cn(
              "mono mr-2 rounded border px-1.5 py-0.5 text-[10.5px]",
              level === "plan" ? "border-status-failed text-status-failed" : "border-status-revision text-status-revision"
            )}
          >
            {tag}
          </span>
          {escalation.reason}
        </div>

        {subtask && (
          <div className="mb-4 grid grid-cols-2 gap-3.5">
            <div className="rounded-lg bg-canvas px-3.5 py-3">
              <div className="mb-1.5 text-[10px] uppercase tracking-wide text-text-faint">Subtask</div>
              <div className="mono text-[12px] leading-relaxed text-text-muted">{subtask.description}</div>
            </div>
            <div className="rounded-lg bg-canvas px-3.5 py-3">
              <div className="mb-1.5 text-[10px] uppercase tracking-wide text-text-faint">Escalation context</div>
              <div className="mono text-[12px] leading-relaxed text-text-muted">
                reason: {tag}
                <br />
                attempt: {subtask.attempt_count}
              </div>
            </div>
          </div>
        )}

        {!takingOver ? (
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              disabled={busy}
              className="bg-status-completed text-badT hover:bg-status-completed/90"
              onClick={() => decide("approve")}
            >
              {level === "plan" ? "Approve plan as-is" : "Approve — let it write"}
            </Button>
            {level === "subtask" && (
              <Button size="sm" disabled={busy} className="bg-role-human text-background hover:bg-role-human/90" onClick={() => setTakingOver(true)}>
                Take over with my output
              </Button>
            )}
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              className="border-status-failed text-status-failed hover:bg-status-failed/10"
              onClick={() => decide("reject")}
            >
              {level === "plan" ? "Reject task" : "Reject subtask"}
            </Button>
          </div>
        ) : (
          <div>
            <div className="mb-1.5 text-[10.5px] uppercase tracking-wide text-text-faint">
              Take over — supply the output yourself instead
            </div>
            <textarea
              className="mb-3 w-full rounded-lg border border-dashed border-border-strong bg-canvas px-3.5 py-2.5 text-[12.5px] text-text placeholder:text-text-faint"
              rows={3}
              placeholder="Type the exact output the agent should use…"
              value={overrideOutput}
              onChange={(e) => setOverrideOutput(e.target.value)}
            />
            <div className="flex gap-2">
              <Button
                size="sm"
                disabled={busy || !overrideOutput.trim()}
                className="bg-role-human text-background hover:bg-role-human/90"
                onClick={() => decide("take_over", overrideOutput.trim())}
              >
                Submit &amp; resume
              </Button>
              <Button size="sm" variant="outline" disabled={busy} onClick={() => setTakingOver(false)}>
                Cancel
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
