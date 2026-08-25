"use client";

import { useParams } from "next/navigation";
import { RunWorkspaceClient } from "@/components/execution/RunWorkspaceClient";

export default function RunDetailPage() {
  const { taskId } = useParams<{ taskId: string }>();
  return <RunWorkspaceClient taskId={taskId} mode="history" />;
}
