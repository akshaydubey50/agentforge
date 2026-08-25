"use client";

import { useRouter } from "next/navigation";
import type { TaskOut } from "@/lib/api";
import { Pill } from "@/components/ui/Pill";
import { taskStatusPill, formatRelativeTime } from "@/lib/statusPill";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";

function duration(start: string, end: string) {
  const ms = Math.max(0, new Date(end).getTime() - new Date(start).getTime());
  if (ms < 1000) return "<1s";
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.round(minutes / 60)}h`;
}

export function RunsTable({ tasks }: { tasks: TaskOut[] }) {
  const router = useRouter();

  return (
    <div className="tbl-scroll rounded-[var(--rm)] border border-border bg-surface">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead>Goal</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Started</TableHead>
            <TableHead>Duration</TableHead>
            <TableHead>Steps</TableHead>
            <TableHead>Tools</TableHead>
            <TableHead>Approvals</TableHead>
            <TableHead>Retries</TableHead>
            <TableHead>Tokens</TableHead>
            <TableHead>Cost</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {tasks.map((t) => {
            const pill = taskStatusPill(t.status);
            const isLive = t.status === "running";
            return (
              <TableRow key={t.id} className="cursor-pointer" onClick={() => router.push(`/runs/${t.id}`)}>
                <TableCell>
                  <div className="max-w-[360px] truncate text-text" title={t.request_text}>
                    {t.request_text}
                  </div>
                </TableCell>
                <TableCell>
                  <Pill kind={pill.kind} live={isLive}>
                    {pill.label}
                  </Pill>
                </TableCell>
                <TableCell className="text-text-muted">{formatRelativeTime(t.created_at)}</TableCell>
                <TableCell className="font-mono text-text-muted">{duration(t.created_at, t.updated_at)}</TableCell>
                <TableCell className="text-text-faint">Open run</TableCell>
                <TableCell className="text-text-faint">Open run</TableCell>
                <TableCell className="text-text-faint">{t.status === "awaiting_approval" ? "Waiting" : "Open run"}</TableCell>
                <TableCell className="text-text-faint">Not exposed</TableCell>
                <TableCell className="text-text-faint">Not exposed</TableCell>
                <TableCell className="text-text-faint">Analytics</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
