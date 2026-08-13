"use client";

import { useEffect } from "react";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { api } from "@/lib/api";
import { isActiveTaskStatus } from "@/lib/agentStatus";
import { LAST_TASK_STORAGE_KEY } from "@/components/shell/Sidebar";
import { TaskDetailHeader } from "@/components/feed/TaskDetailHeader";
import { AgentGraphView } from "@/components/graph/AgentGraphView";
import { SkeletonRows } from "@/components/ui/Skeleton";

export default function AgentGraphPage() {
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

  if (!task) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex-1 overflow-y-auto p-5.5">
          <SkeletonRows rows={4} />
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <TaskDetailHeader task={task} view="graph" />
      <AgentGraphView task={task} spans={spans ?? []} escalations={escalations?.items ?? []} />
    </div>
  );
}
