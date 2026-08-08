"use client";

import { useParams, useRouter } from "next/navigation";
import useSWR from "swr";
import { api, isActiveStatus } from "@/lib/api";
import { TopBar } from "@/components/shell/TopBar";
import { Pill } from "@/components/ui/Pill";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { TraceTimeline } from "@/components/runs/TraceTimeline";
import { PipelineGraph } from "@/components/runs/PipelineGraph";
import { taskStatusPill } from "@/lib/statusPill";
import { Button } from "@/components/ui/button";

export default function RunDetailPage() {
  const { taskId } = useParams<{ taskId: string }>();
  const router = useRouter();

  const { data: task } = useSWR(["task", taskId], () => api.getTask(taskId), {
    refreshInterval: (data) => (data && isActiveStatus(data.status) ? 1500 : 0),
  });
  const { data: spans } = useSWR(["trace", taskId], () => api.getTrace(taskId), {
    refreshInterval: (data) => (task && isActiveStatus(task.status) ? 1500 : 0),
  });

  if (!task) {
    return (
      <>
        <TopBar
          title="Run"
          subtitle="loading…"
          actions={
            <Button variant="outline" size="sm" onClick={() => router.push("/runs")}>
              Back
            </Button>
          }
        />
        <div className="flex-1 overflow-y-auto px-5 py-5">
          <SkeletonRows />
        </div>
      </>
    );
  }

  const pill = taskStatusPill(task.status);

  return (
    <>
      <TopBar
        title="Run detail"
        actions={
          <Button variant="outline" size="sm" onClick={() => router.push("/runs")}>
            Back to Runs
          </Button>
        }
      />
      <div className="flex-1 overflow-y-auto px-5 py-5">
        <div className="mb-3.5 rounded-[var(--rm)] border border-border bg-surface px-5 py-4.5">
          <div className="flex flex-wrap items-center gap-2.5">
            <Pill kind={pill.kind} live={task.status === "running"}>
              {pill.label}
            </Pill>
            <span className="mono text-[11.5px] text-text-faint">run {task.id.slice(0, 8)}</span>
            <span className="ml-auto text-[12px] text-text-muted">
              {task.subtasks.length} step{task.subtasks.length === 1 ? "" : "s"}
            </span>
          </div>
          <h3 className="font-serif-display mt-2.5 text-[18px] font-semibold tracking-tight text-text">
            {task.request_text}
          </h3>
          <div className="mt-1 text-[12px] text-text-faint">
            Started {new Date(task.created_at).toLocaleString()}
            {task.status === "completed" && ` · finished ${new Date(task.updated_at).toLocaleTimeString()}`}
          </div>
          {task.final_output && (
            <div className="animate-settle-in mt-3.5 rounded-[10px] bg-ok-wash px-3.5 py-3 text-[13px] text-text">
              <b className="mb-1 block text-[10px] font-extrabold uppercase tracking-wide text-ok">Final answer</b>
              {task.final_output}
            </div>
          )}
        </div>

        {task.subtasks.length > 0 && (
          <>
            <h3 className="mb-2.5 text-[11px] font-extrabold uppercase tracking-wide text-text-faint">
              Pipeline — {isActiveStatus(task.status) ? "current stage" : "how it was structured"}
            </h3>
            <div className="mb-4">
              <PipelineGraph subtasks={task.subtasks} />
            </div>
          </>
        )}

        <h3 className="mb-2.5 text-[11px] font-extrabold uppercase tracking-wide text-text-faint">Trace</h3>
        {spans && spans.length > 0 ? <TraceTimeline spans={spans} /> : <SkeletonRows rows={2} />}
      </div>
    </>
  );
}
