"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { api, type EscalationDecision } from "@/lib/api";
import { isActiveTaskStatus } from "@/lib/agentStatus";
import { buildRunModel } from "@/lib/execution/model";
import { useTaskEvents } from "@/lib/useTaskEvents";
import { LAST_TASK_STORAGE_KEY } from "@/components/shell/Sidebar";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { WorkspaceShell } from "./WorkspaceShell";

function shouldPoll(status: string | undefined) {
  return status === "pending" || status === "running" || status === "awaiting_approval";
}

export function RunWorkspaceClient({ taskId, mode }: { taskId: string; mode: "live" | "history" }) {
  const [sending, setSending] = useState(false);
  const [deciding, setDeciding] = useState(false);

  const taskSWR = useSWR(["task", taskId], () => api.getTask(taskId), {
    refreshInterval: (data) => (shouldPoll(data?.status) ? 1800 : 0),
  });
  const traceSWR = useSWR(["trace", taskId], () => api.getTrace(taskId), {
    refreshInterval: () => (shouldPoll(taskSWR.data?.status) ? 2200 : 0),
  });
  const escalationSWR = useSWR(["task-escalations-all", taskId], () => api.listEscalations("all", 75, 0, taskId), {
    refreshInterval: () => (shouldPoll(taskSWR.data?.status) ? 3000 : 0),
  });
  const artifactSWR = useSWR(["task-artifacts", taskId], () => api.listArtifacts(taskId), {
    refreshInterval: () => (shouldPoll(taskSWR.data?.status) ? 5000 : 0),
  });

  useEffect(() => {
    try {
      localStorage.setItem(LAST_TASK_STORAGE_KEY, taskId);
    } catch {
      // The Workspace can run without localStorage; this only improves nav recovery.
    }
  }, [taskId]);

  const refreshDurableState = useCallback(() => {
    taskSWR.mutate();
    traceSWR.mutate();
    escalationSWR.mutate();
    artifactSWR.mutate();
  }, [artifactSWR, escalationSWR, taskSWR, traceSWR]);

  const stream = useTaskEvents(taskId, {
    enabled: mode === "live" && shouldPoll(taskSWR.data?.status),
    onEvent: refreshDurableState,
  });

  useEffect(() => {
    if (stream.state === "open" || stream.state === "error") refreshDurableState();
  }, [stream.state, refreshDurableState]);

  const model = useMemo(() => {
    if (!taskSWR.data) return null;
    return buildRunModel({
      task: taskSWR.data,
      spans: traceSWR.data ?? [],
      escalations: escalationSWR.data?.items ?? [],
      artifacts: artifactSWR.data?.files ?? [],
      liveEvents: stream.events,
    });
  }, [artifactSWR.data?.files, escalationSWR.data?.items, stream.events, taskSWR.data, traceSWR.data]);

  const sendMessage = async (content: string) => {
    setSending(true);
    try {
      await api.sendTaskMessage(taskId, content);
      refreshDurableState();
    } finally {
      setSending(false);
    }
  };

  const decideApproval = async (escalationId: string, decision: EscalationDecision) => {
    setDeciding(true);
    try {
      await api.decideEscalation(escalationId, decision);
      refreshDurableState();
    } finally {
      setDeciding(false);
    }
  };

  if (taskSWR.error) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center bg-surface p-6">
        <section className="max-w-[520px] rounded-[8px] border border-border bg-rail p-5 text-center">
          <h1 className="text-[16px] font-semibold text-text">Run unavailable</h1>
          <p className="mt-2 text-[12.5px] leading-relaxed text-text-muted">
            The durable task record could not be loaded. Check the API session, backend health, or whether this run still exists.
          </p>
          <div className="mt-3 font-mono text-[11px] text-text-faint">run #{taskId.slice(0, 8)}</div>
        </section>
      </div>
    );
  }

  if (!model) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex-1 overflow-y-auto p-5">
          <SkeletonRows rows={4} />
        </div>
      </div>
    );
  }

  const connectionState =
    mode === "history" || (model.task.status !== "awaiting_approval" && !isActiveTaskStatus(model.task.status))
      ? "complete"
      : stream.state === "open"
        ? "connected"
        : stream.state === "error"
          ? "disconnected"
          : "reconnecting";

  return (
    <WorkspaceShell
      model={model}
      mode={mode}
      connectionState={connectionState}
      sending={sending}
      deciding={deciding}
      onSendMessage={sendMessage}
      onDecideApproval={decideApproval}
    />
  );
}
