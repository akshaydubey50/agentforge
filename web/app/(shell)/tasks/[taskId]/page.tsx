"use client";

import { useEffect } from "react";
import { useParams } from "next/navigation";
import useSWR, { mutate } from "swr";
import { api } from "@/lib/api";
import { isActiveTaskStatus } from "@/lib/agentStatus";
import { buildFeed } from "@/lib/traceFeed";
import { LAST_TASK_STORAGE_KEY } from "@/components/shell/Sidebar";
import { TaskDetailHeader } from "@/components/feed/TaskDetailHeader";
import { TaskContextPanel } from "@/components/feed/TaskContextPanel";
import { FeedMessage } from "@/components/feed/FeedMessage";
import { FeedEscalationGate } from "@/components/feed/FeedEscalationGate";
import { SkeletonRows } from "@/components/ui/Skeleton";

export default function TaskFeedPage() {
  const { taskId } = useParams<{ taskId: string }>();

  const { data: task } = useSWR(["task", taskId], () => api.getTask(taskId), {
    refreshInterval: (data) => (data && isActiveTaskStatus(data.status) ? 1500 : data?.status === "awaiting_approval" ? 4000 : 0),
  });
  const { data: spans } = useSWR(["trace", taskId], () => api.getTrace(taskId), {
    refreshInterval: () => (task && (isActiveTaskStatus(task.status) || task.status === "awaiting_approval") ? 2000 : 0),
  });
  const { data: escalations } = useSWR(["task-escalations-all", taskId], () => api.listEscalations("all", 50, 0, taskId), {
    refreshInterval: () => (task && (isActiveTaskStatus(task.status) || task.status === "awaiting_approval") ? 4000 : 0),
  });

  useEffect(() => {
    try {
      localStorage.setItem(LAST_TASK_STORAGE_KEY, taskId);
    } catch {
      // localStorage unavailable -- Sidebar's Graph link just falls back to /tasks
    }
  }, [taskId]);

  const decide = async (escalationId: string, decision: "approve" | "reject" | "take_over", overrideOutput?: string) => {
    await api.decideEscalation(escalationId, decision, { overrideOutput });
    mutate(["task", taskId]);
    mutate(["trace", taskId]);
    mutate(["task-escalations-all", taskId]);
    mutate("pending-escalations-count");
  };

  if (!task) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex-1 overflow-y-auto p-5.5">
          <SkeletonRows rows={4} />
        </div>
      </div>
    );
  }

  const items = buildFeed(spans ?? [], task.subtasks, escalations?.items ?? []);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <TaskDetailHeader task={task} view="feed" />
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col border-r border-border">
          <div className="flex-1 overflow-y-auto px-5.5 py-5.5">
            <div className="mb-4.5 text-center text-[11px] text-text-faint">
              Task submitted · {items.length} event{items.length === 1 ? "" : "s"} · human approval enabled
            </div>
            <div className="space-y-4.5">
              {items.map((item) =>
                item.kind === "escalation" ? (
                  <FeedEscalationGate
                    key={item.key}
                    escalation={item.escalation!}
                    onDecide={(decision, output) => decide(item.escalation!.id, decision, output)}
                  />
                ) : (
                  <FeedMessage key={item.key} item={item} />
                )
              )}
              {items.length === 0 && <div className="text-center text-[12.5px] text-text-faint">Waiting on the first trace span…</div>}
            </div>
          </div>
        </div>
        <TaskContextPanel task={task} spans={spans ?? []} />
      </div>
    </div>
  );
}
