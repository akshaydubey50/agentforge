"use client";

import { useRouter } from "next/navigation";
import type { TaskOut } from "@/lib/api";
import { Pill } from "@/components/ui/Pill";
import { taskStatusPill, formatRelativeTime } from "@/lib/statusPill";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";

export function RunsTable({ tasks }: { tasks: TaskOut[] }) {
  const router = useRouter();

  return (
    <div className="tbl-scroll rounded-[var(--rm)] border border-border bg-surface">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead>Task</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>When</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {tasks.map((t) => {
            const pill = taskStatusPill(t.status);
            const isLive = t.status === "running";
            return (
              <TableRow key={t.id} className="cursor-pointer" onClick={() => router.push(`/runs/${t.id}`)}>
                <TableCell>
                  <div className="max-w-[420px] truncate text-text" title={t.request_text}>
                    {t.request_text}
                  </div>
                </TableCell>
                <TableCell>
                  <Pill kind={pill.kind} live={isLive}>
                    {pill.label}
                  </Pill>
                </TableCell>
                <TableCell className="text-text-muted">{formatRelativeTime(t.created_at)}</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
