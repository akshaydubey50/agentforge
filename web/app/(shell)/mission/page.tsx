"use client";

import { useRouter } from "next/navigation";
import type { ReactNode } from "react";
import useSWR from "swr";
import { AlertTriangle, ArrowRight, CheckCircle2, Clock3, GitBranch, ShieldAlert, Wrench } from "lucide-react";
import { api } from "@/lib/api";
import { formatRelativeTime, taskStatusPill } from "@/lib/statusPill";
import { Pill } from "@/components/ui/Pill";
import { SkeletonRows } from "@/components/ui/Skeleton";

export default function MissionControlPage() {
  const router = useRouter();
  const { data: summary } = useSWR("system-summary", () => api.getSystemSummary(), { refreshInterval: 5000 });
  const { data: recent } = useSWR(["mission-recent-runs"], () => api.listTasks(8, 0, "all"), { refreshInterval: 5000 });
  const { data: failures } = useSWR(["mission-failed-runs"], () => api.listTasks(4, 0, "failed"), { refreshInterval: 8000 });
  const { data: approvals } = useSWR(["mission-approvals"], () => api.listEscalations("pending", 4, 0), { refreshInterval: 5000 });
  const { data: google } = useSWR("google-status", () => api.getGoogleStatus());

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-surface">
      <header className="border-b border-border px-6 py-5">
        <div className="text-[11px] uppercase tracking-[0.12em] text-text-faint">Mission Control</div>
        <h1 className="mt-1 text-[22px] font-semibold tracking-tight text-text">Start, resume, or intervene</h1>
        <p className="mt-1 max-w-[78ch] text-[13px] leading-relaxed text-text-muted">
          Operational landing page for active runs, approvals, recent failures, memory and tool health.
        </p>
      </header>

      <main className="grid min-h-0 flex-1 grid-cols-[minmax(430px,1fr)_minmax(320px,0.55fr)] gap-5 overflow-y-auto p-6 max-[1080px]:grid-cols-1">
        <section className="space-y-4">
          <Panel title="Active work" icon={<GitBranch className="h-4 w-4" />}>
            {!recent && <SkeletonRows rows={3} />}
            {(recent?.items ?? [])
              .filter((task) => task.status === "pending" || task.status === "running" || task.status === "awaiting_approval")
              .slice(0, 4)
              .map((task) => (
                <RunRow key={task.id} task={task} onOpen={() => router.push(`/workspace?run=${task.id}`)} />
              ))}
            {recent && recent.items.filter((task) => task.status === "pending" || task.status === "running" || task.status === "awaiting_approval").length === 0 && (
              <EmptyLine>No active runs. Start from Workspace.</EmptyLine>
            )}
          </Panel>

          <Panel title="Recent failures needing attention" icon={<AlertTriangle className="h-4 w-4" />}>
            {!failures && <SkeletonRows rows={2} />}
            {(failures?.items ?? []).map((task) => (
              <RunRow key={task.id} task={task} onOpen={() => router.push(`/runs/${task.id}`)} />
            ))}
            {failures && failures.items.length === 0 && <EmptyLine>No recent failed runs.</EmptyLine>}
          </Panel>

          <Panel title="Recent completed work" icon={<CheckCircle2 className="h-4 w-4" />}>
            {!recent && <SkeletonRows rows={3} />}
            {(recent?.items ?? [])
              .filter((task) => task.status === "completed")
              .slice(0, 4)
              .map((task) => (
                <RunRow key={task.id} task={task} onOpen={() => router.push(`/runs/${task.id}`)} />
              ))}
            {recent && recent.items.filter((task) => task.status === "completed").length === 0 && <EmptyLine>No completed runs yet.</EmptyLine>}
          </Panel>
        </section>

        <aside className="space-y-4">
          <Panel title="Waiting approvals" icon={<ShieldAlert className="h-4 w-4" />}>
            {(approvals?.items ?? []).map((approval) => (
              <button
                key={approval.id}
                type="button"
                onClick={() => router.push(`/workspace?run=${approval.task_id}`)}
                className="flex w-full items-center gap-3 border-b border-border px-4 py-3 text-left last:border-b-0 hover:bg-surface-2"
              >
                <ShieldAlert className="h-4 w-4 flex-none text-role-human" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12.5px] font-semibold text-text">{approval.reason}</span>
                  <span className="font-mono text-[11px] text-text-faint">Run #{approval.task_id.slice(0, 8)}</span>
                </span>
                <ArrowRight className="h-3.5 w-3.5 text-text-faint" />
              </button>
            ))}
            {approvals && approvals.items.length === 0 && <EmptyLine>No human decisions pending.</EmptyLine>}
          </Panel>

          <Panel title="System health" icon={<Wrench className="h-4 w-4" />}>
            <HealthLine label="Active runs" value={summary ? String(summary.tasks.active) : "Loading"} />
            <HealthLine label="Pending approvals" value={summary ? String(summary.approvals_pending) : "Loading"} />
            <HealthLine label="Registered tools" value={summary ? String(summary.tools_registered) : "Loading"} />
            <HealthLine label="Memory entries" value={summary ? String(summary.memory_entries) : "Loading"} />
            <HealthLine label="Google integration" value={google ? (google.connected ? "Connected" : google.configured ? "Needs consent" : "Server not configured") : "Loading"} />
          </Panel>

          <Panel title="Quick starts" icon={<Clock3 className="h-4 w-4" />}>
            {[
              "Research current AI developments and verify sources.",
              "Prepare a Gmail draft after approval.",
              "Use knowledge and memory to continue architecture work.",
              "Investigate recent failed runs.",
            ].map((prompt) => (
              <button
                key={prompt}
                type="button"
                onClick={() => router.push(`/workspace?prompt=${encodeURIComponent(prompt)}`)}
                className="block w-full border-b border-border px-4 py-3 text-left text-[12.5px] text-text-muted transition last:border-b-0 hover:bg-surface-2 hover:text-text"
              >
                {prompt}
              </button>
            ))}
          </Panel>
        </aside>
      </main>
    </div>
  );
}

function Panel({ title, icon, children }: { title: string; icon: ReactNode; children: ReactNode }) {
  return (
    <section className="overflow-hidden rounded-[8px] border border-border bg-rail">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3 text-text">
        {icon}
        <h2 className="text-[13px] font-semibold">{title}</h2>
      </div>
      <div>{children}</div>
    </section>
  );
}

function RunRow({ task, onOpen }: { task: { id: string; request_text: string; status: Parameters<typeof taskStatusPill>[0]; created_at: string }; onOpen: () => void }) {
  const pill = taskStatusPill(task.status);
  return (
    <button type="button" onClick={onOpen} className="flex w-full items-start gap-3 border-b border-border px-4 py-3 text-left last:border-b-0 hover:bg-surface-2">
      <GitBranch className="mt-0.5 h-4 w-4 flex-none text-text-faint" />
      <span className="min-w-0 flex-1">
        <span className="line-clamp-2 text-[12.5px] font-semibold leading-snug text-text">{task.request_text}</span>
        <span className="mt-1 flex items-center gap-2">
          <Pill kind={pill.kind} live={task.status === "running"}>
            {pill.label}
          </Pill>
          <span className="text-[11px] text-text-faint">{formatRelativeTime(task.created_at)}</span>
        </span>
      </span>
      <ArrowRight className="mt-1 h-3.5 w-3.5 flex-none text-text-faint" />
    </button>
  );
}

function HealthLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between border-b border-border px-4 py-2.5 text-[12px] last:border-b-0">
      <span className="text-text-muted">{label}</span>
      <span className="font-mono text-text">{value}</span>
    </div>
  );
}

function EmptyLine({ children }: { children: ReactNode }) {
  return <div className="px-4 py-5 text-[12px] text-text-faint">{children}</div>;
}
