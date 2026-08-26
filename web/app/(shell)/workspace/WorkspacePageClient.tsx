"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import { ArrowRight, Clock3, GitBranch, ShieldAlert, Sparkles } from "lucide-react";
import { API_BASE_URL, api } from "@/lib/api";
import { formatRelativeTime, taskStatusPill } from "@/lib/statusPill";
import { cn } from "@/lib/utils";
import { LAST_TASK_STORAGE_KEY } from "@/components/shell/Sidebar";
import { RunWorkspaceClient } from "@/components/execution/RunWorkspaceClient";
import { Pill } from "@/components/ui/Pill";

const DEMO_PROMPTS = [
  "Find the top Agentic AI developments today, verify them, and create LinkedIn content ideas.",
  "Search my Gmail for the recruiter conversation, understand the role, and prepare a reply draft.",
  "Search internal knowledge and explain our hiring workflow.",
  "Investigate why recent agent runs failed.",
];

export function WorkspacePageClient() {
  const router = useRouter();
  const params = useSearchParams();
  const taskId = params.get("new") === "1" ? null : params.get("run");
  const promptParam = params.get("prompt");
  const [prompt, setPrompt] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const { data: recent } = useSWR(["workspace-recent-runs"], () => api.listTasks(8, 0, "all"), {
    refreshInterval: 5000,
  });
  const { data: approvals } = useSWR(["workspace-pending-approvals"], () => api.listEscalations("pending", 4, 0), {
    refreshInterval: 5000,
  });

  useEffect(() => {
    if (promptParam && !taskId) setPrompt(promptParam);
  }, [promptParam, taskId]);

  useEffect(() => {
    if (params.get("new") !== "1") return;
    try {
      localStorage.removeItem(LAST_TASK_STORAGE_KEY);
    } catch {
      // The new-run route still works without localStorage.
    }
  }, [params]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const value = prompt.trim();
    if (!value || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const task = await api.createTask(value);
      try {
        localStorage.setItem(LAST_TASK_STORAGE_KEY, task.id);
      } catch {
        // Best-effort nav recovery only.
      }
      router.push(`/workspace?run=${task.id}`);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "Unable to start the run.");
    } finally {
      setSubmitting(false);
    }
  };

  if (taskId) return <RunWorkspaceClient taskId={taskId} mode="live" />;

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-surface">
      <header className="border-b border-border px-6 py-5">
        <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.12em] text-text-faint">
          <Sparkles className="h-3.5 w-3.5" />
          AgentForge Execution Studio
        </div>
        <h1 className="mt-2 text-[22px] font-semibold tracking-tight text-text">Start or resume a live agent run</h1>
        <p className="mt-1 max-w-[78ch] text-[13px] leading-relaxed text-text-muted">
          Workspace is the primary surface: chat, execution graph, timeline, inspector, and approvals all attach to one run identity.
        </p>
      </header>

      <main className="grid min-h-0 flex-1 grid-cols-[minmax(460px,0.95fr)_minmax(360px,0.65fr)] gap-5 overflow-y-auto p-6 max-[1080px]:grid-cols-1">
        <section className="min-w-0">
          <form onSubmit={submit} className="rounded-[8px] border border-border bg-rail p-4">
            <label className="text-[12px] font-semibold text-text" htmlFor="workspace-goal">
              Give the agent a goal
            </label>
            <textarea
              id="workspace-goal"
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="Ask AgentForge to research, inspect memory, use tools, request approval, and verify the result..."
              className="mt-2 min-h-[132px] w-full resize-none rounded-[8px] border border-border bg-background px-3 py-2 text-[13px] leading-relaxed text-text outline-none transition focus:border-role-supervisor"
            />
            <div className="mt-3 flex items-center justify-between gap-3">
              <span className="text-[11.5px] text-text-faint">A new run opens directly in Chat + Live Execution Graph.</span>
              <button
                type="submit"
                disabled={!prompt.trim() || submitting}
                className="inline-flex items-center gap-1.5 rounded-[7px] bg-role-supervisor px-3.5 py-2 text-[12px] font-semibold text-background transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-45"
              >
                {submitting ? "Starting" : "Run"}
                <ArrowRight className="h-3.5 w-3.5" />
              </button>
            </div>
            {submitError && (
              <div className="mt-3 rounded-[7px] border border-status-failed/45 bg-status-failed/10 px-3 py-2 text-[12px] leading-relaxed text-status-failed" role="alert">
                Could not start the run against {API_BASE_URL}. {submitError}
              </div>
            )}
          </form>

          <div className="mt-5 grid grid-cols-2 gap-3 max-[760px]:grid-cols-1">
            {DEMO_PROMPTS.map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => setPrompt(item)}
                className="rounded-[8px] border border-border bg-surface px-3.5 py-3 text-left text-[12.5px] leading-relaxed text-text-muted transition hover:border-role-supervisor/50 hover:text-text"
              >
                {item}
              </button>
            ))}
          </div>
        </section>

        <aside className="min-w-0 space-y-4">
          <section className="rounded-[8px] border border-border bg-rail">
            <div className="flex items-center gap-2 border-b border-border px-4 py-3">
              <ShieldAlert className="h-4 w-4 text-role-human" />
              <div className="text-[13px] font-semibold text-text">Waiting approvals</div>
            </div>
            <div className="divide-y divide-border">
              {(approvals?.items ?? []).map((approval) => (
                <button
                  key={approval.id}
                  type="button"
                  onClick={() => router.push(`/workspace?run=${approval.task_id}`)}
                  className="block w-full px-4 py-3 text-left transition hover:bg-surface-2"
                >
                  <div className="truncate text-[12.5px] font-semibold text-text">{approval.reason}</div>
                  <div className="mt-1 font-mono text-[11px] text-text-faint">Run #{approval.task_id.slice(0, 8)}</div>
                </button>
              ))}
              {approvals && approvals.items.length === 0 && <div className="px-4 py-5 text-[12px] text-text-faint">No approvals waiting.</div>}
              {!approvals && <div className="px-4 py-5 text-[12px] text-text-faint">Loading approvals...</div>}
            </div>
          </section>

          <section className="rounded-[8px] border border-border bg-rail">
            <div className="flex items-center gap-2 border-b border-border px-4 py-3">
              <Clock3 className="h-4 w-4 text-text-muted" />
              <div className="text-[13px] font-semibold text-text">Recent runs</div>
            </div>
            <div className="divide-y divide-border">
              {(recent?.items ?? []).map((task) => {
                const pill = taskStatusPill(task.status);
                return (
                  <button
                    key={task.id}
                    type="button"
                    onClick={() => router.push(`/workspace?run=${task.id}`)}
                    className="block w-full px-4 py-3 text-left transition hover:bg-surface-2"
                  >
                    <div className="flex items-start gap-3">
                      <GitBranch className={cn("mt-0.5 h-3.5 w-3.5 flex-none", task.status === "running" ? "text-status-running" : "text-text-faint")} />
                      <div className="min-w-0 flex-1">
                        <div className="line-clamp-2 text-[12.5px] font-semibold leading-snug text-text">{task.request_text}</div>
                        <div className="mt-1 flex items-center gap-2">
                          <Pill kind={pill.kind} live={task.status === "running"}>
                            {pill.label}
                          </Pill>
                          <span className="text-[11px] text-text-faint">{formatRelativeTime(task.created_at)}</span>
                        </div>
                      </div>
                    </div>
                  </button>
                );
              })}
              {recent && recent.items.length === 0 && <div className="px-4 py-5 text-[12px] text-text-faint">No runs yet.</div>}
              {!recent && <div className="px-4 py-5 text-[12px] text-text-faint">Loading recent runs...</div>}
            </div>
          </section>
        </aside>
      </main>
    </div>
  );
}
