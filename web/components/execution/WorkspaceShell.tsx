"use client";

import { useEffect, useMemo, useState } from "react";
import { GitBranch, Maximize2, MessageSquare, PanelRightClose, PanelRightOpen } from "lucide-react";
import type { EscalationDecision } from "@/lib/api";
import type { ExecutionNode, RunModel } from "@/lib/execution/types";
import { canContinueConversation } from "@/lib/agentStatus";
import { cn } from "@/lib/utils";
import { ApprovalPanel } from "./ApprovalPanel";
import { ConversationPanel } from "./ConversationPanel";
import { ExecutionCanvas } from "./ExecutionCanvas";
import { NodeInspector } from "./NodeInspector";
import { RunTimeline } from "./RunTimeline";

export function WorkspaceShell({
  model,
  mode,
  connectionState,
  sending,
  deciding,
  onSendMessage,
  onDecideApproval,
}: {
  model: RunModel;
  mode: "live" | "history";
  connectionState?: "connected" | "reconnecting" | "disconnected" | "complete";
  sending: boolean;
  deciding: boolean;
  onSendMessage: (content: string) => Promise<void>;
  onDecideApproval: (escalationId: string, decision: EscalationDecision) => Promise<void>;
}) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(model.currentNodeId);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [chatOpen, setChatOpen] = useState(true);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [focus, setFocus] = useState<"balanced" | "graph" | "chat">("balanced");

  const selectedNode: ExecutionNode | null = useMemo(
    () => model.nodes.find((node) => node.id === selectedNodeId) ?? null,
    [model.nodes, selectedNodeId]
  );

  const pendingApproval = model.pendingApproval;
  const runStatus = model.task.status.replace(/_/g, " ");
  const canSend = mode === "live" && canContinueConversation(model.task.status);

  useEffect(() => {
    setSelectedNodeId(model.currentNodeId);
    setSelectedEventId(null);
  }, [model.task.id, model.currentNodeId]);

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-surface">
      <header className="flex h-[58px] flex-none items-center gap-3 border-b border-border px-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <div className="truncate text-[14px] font-semibold text-text">
              {mode === "live" ? "Workspace" : "Run Detail"} - #{model.task.id.slice(0, 8)}
            </div>
            <span className="rounded-full border border-border bg-surface-2 px-2 py-0.5 text-[10.5px] uppercase tracking-[0.08em] text-text-faint">
              {runStatus}
            </span>
            {connectionState && (
              <span
                className={cn(
                  "rounded-full px-2 py-0.5 text-[10.5px] uppercase tracking-[0.08em]",
                  connectionState === "connected" ? "bg-ok-wash text-ok" : connectionState === "complete" ? "bg-surface-3 text-text-faint" : "bg-warn-wash text-warn"
                )}
              >
                {connectionState}
              </span>
            )}
          </div>
          <div className="mt-0.5 truncate text-[11.5px] text-text-muted">{model.task.request_text}</div>
        </div>

        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => setChatOpen((value) => !value)}
            aria-label={chatOpen ? "Collapse chat" : "Expand chat"}
            className="rounded-[7px] border border-border bg-surface-2 p-2 text-text-muted transition hover:text-text"
          >
            <MessageSquare className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => setFocus((value) => (value === "graph" ? "balanced" : "graph"))}
            aria-label="Toggle graph focus"
            className={cn(
              "rounded-[7px] border border-border bg-surface-2 p-2 text-text-muted transition hover:text-text",
              focus === "graph" && "border-role-supervisor text-role-supervisor"
            )}
          >
            <Maximize2 className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => setInspectorOpen((value) => !value)}
            aria-label={inspectorOpen ? "Collapse inspector" : "Expand inspector"}
            className="rounded-[7px] border border-border bg-surface-2 p-2 text-text-muted transition hover:text-text"
          >
            {inspectorOpen ? <PanelRightClose className="h-4 w-4" /> : <PanelRightOpen className="h-4 w-4" />}
          </button>
        </div>
      </header>

      {pendingApproval && pendingApproval.status === "pending" && (
        <div className="flex-none border-b border-border bg-rail px-4 py-3">
          <ApprovalPanel escalation={pendingApproval} deciding={deciding} onDecide={(decision) => onDecideApproval(pendingApproval.id, decision)} />
        </div>
      )}

      <div className="grid min-h-0 flex-1 grid-rows-[minmax(0,1fr)_166px] lg:grid-rows-[minmax(0,1fr)_174px]">
        <div
          className={cn(
            "grid min-h-0",
            chatOpen && inspectorOpen && focus === "balanced" && "grid-cols-[minmax(286px,0.72fr)_minmax(420px,1.5fr)_344px]",
            chatOpen && inspectorOpen && focus === "graph" && "grid-cols-[72px_minmax(560px,1fr)_344px]",
            chatOpen && !inspectorOpen && "grid-cols-[minmax(292px,0.62fr)_minmax(520px,1.5fr)]",
            !chatOpen && inspectorOpen && "grid-cols-[minmax(580px,1fr)_344px]",
            !chatOpen && !inspectorOpen && "grid-cols-1",
            "max-[1100px]:grid-cols-1"
          )}
        >
          {chatOpen && (
            <div className={cn("min-h-0 border-r border-border max-[1100px]:hidden", focus === "graph" && "overflow-hidden")}>
              {focus === "graph" ? (
                <button
                  type="button"
                  onClick={() => setFocus("balanced")}
                  className="flex h-full w-full items-center justify-center bg-rail text-text-faint hover:text-text"
                  aria-label="Expand chat"
                >
                  <MessageSquare className="h-4 w-4" />
                </button>
              ) : (
                <ConversationPanel
                  cards={model.cards}
                  selectedNodeId={selectedNodeId}
                  canSend={canSend}
                  sending={sending}
                  onSelectNode={setSelectedNodeId}
                  onSend={onSendMessage}
                />
              )}
            </div>
          )}

          <main className="min-h-0 min-w-0">
            <div className="flex h-[36px] items-center gap-2 border-b border-border bg-rail px-4">
              <GitBranch className="h-3.5 w-3.5 text-text-muted" />
              <span className="text-[12px] font-semibold text-text">Live Execution Graph</span>
              <span className="text-[11px] text-text-faint">
                {mode === "live" ? "Grows from runtime events and durable state" : "Execution history reconstructed from stored run data"}
              </span>
            </div>
            <div className="h-[calc(100%-36px)]">
              <ExecutionCanvas
                nodes={model.nodes}
                edges={model.edges}
                events={model.events}
                selectedNodeId={selectedNodeId}
                selectedEventId={selectedEventId}
                onSelectNode={setSelectedNodeId}
              />
            </div>
          </main>

          <div className="max-[1100px]:hidden">
            <NodeInspector
              model={model}
              selectedNode={selectedNode}
              open={inspectorOpen}
              deciding={deciding}
              onClose={() => setInspectorOpen(false)}
              onDecide={onDecideApproval}
            />
          </div>
        </div>

        <RunTimeline
          events={model.events}
          runStartedAt={model.task.created_at}
          selectedEventId={selectedEventId}
          selectedNodeId={selectedNodeId}
          onSelectEvent={setSelectedEventId}
          onSelectNode={setSelectedNodeId}
        />
      </div>
    </div>
  );
}
