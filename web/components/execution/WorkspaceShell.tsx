"use client";

import { KeyboardEvent, PointerEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { GitBranch, Maximize2, MessageSquare, PanelRightClose, PanelRightOpen, PlusCircle } from "lucide-react";
import type { EscalationDecision } from "@/lib/api";
import type { ExecutionNode, RunModel } from "@/lib/execution/types";
import { canContinueConversation } from "@/lib/agentStatus";
import { cn } from "@/lib/utils";
import { ApprovalPanel } from "./ApprovalPanel";
import { ConversationPanel } from "./ConversationPanel";
import { ExecutionCanvas } from "./ExecutionCanvas";
import { NodeInspector } from "./NodeInspector";
import { RunTimeline } from "./RunTimeline";

const PANE_LAYOUT_STORAGE_KEY = "agentforge-workspace-pane-layout-v2";
const CHAT_MIN_WIDTH = 340;
const CHAT_DEFAULT_WIDTH = 440;
const CHAT_MAX_WIDTH = 720;
const CHAT_RAIL_WIDTH = 60;
const GRAPH_MIN_WIDTH = 520;
const INSPECTOR_MIN_WIDTH = 300;
const INSPECTOR_DEFAULT_WIDTH = 344;
const INSPECTOR_MAX_WIDTH = 480;
const SPLITTER_WIDTH = 6;
const DESKTOP_QUERY = "(min-width: 1280px)";

function clamp(value: number, min: number, max: number) {
  if (max < min) return min;
  return Math.min(Math.max(value, min), max);
}

function readStoredPaneLayout() {
  if (typeof window === "undefined") {
    return { chatWidth: CHAT_DEFAULT_WIDTH, inspectorWidth: INSPECTOR_DEFAULT_WIDTH };
  }
  try {
    const [storedChat, storedInspector] = (window.localStorage.getItem(PANE_LAYOUT_STORAGE_KEY) || "").split(":");
    const parsedChat = Number(storedChat);
    const parsedInspector = Number(storedInspector);
    return {
      chatWidth: Number.isFinite(parsedChat) ? clamp(parsedChat, CHAT_MIN_WIDTH, CHAT_MAX_WIDTH) : CHAT_DEFAULT_WIDTH,
      inspectorWidth: Number.isFinite(parsedInspector)
        ? clamp(parsedInspector, INSPECTOR_MIN_WIDTH, INSPECTOR_MAX_WIDTH)
        : INSPECTOR_DEFAULT_WIDTH,
    };
  } catch {
    return { chatWidth: CHAT_DEFAULT_WIDTH, inspectorWidth: INSPECTOR_DEFAULT_WIDTH };
  }
}

function PaneSplitter({
  label,
  onPointerDown,
  onKeyDown,
}: {
  label: string;
  onPointerDown: (event: PointerEvent<HTMLDivElement>) => void;
  onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void;
}) {
  return (
    <div
      role="separator"
      aria-label={label}
      aria-orientation="vertical"
      tabIndex={0}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      className="group relative z-20 h-full w-[6px] cursor-col-resize bg-border/45 outline-none transition hover:bg-role-supervisor/40 focus-visible:bg-role-supervisor/55 max-[1279px]:hidden"
    >
      <div className="absolute left-1/2 top-1/2 h-10 w-[2px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-border-strong transition group-hover:bg-role-supervisor group-focus-visible:bg-role-supervisor" />
    </div>
  );
}

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
  const [timelineOpen, setTimelineOpen] = useState(true);
  const [focus, setFocus] = useState<"balanced" | "graph" | "chat">("balanced");
  const [{ chatWidth, inspectorWidth }, setPaneLayout] = useState(readStoredPaneLayout);
  const [containerWidth, setContainerWidth] = useState(0);
  const [isDesktop, setIsDesktop] = useState(false);
  const paneGridRef = useRef<HTMLDivElement | null>(null);
  const activeDragRef = useRef<{
    kind: "chat" | "inspector";
    startX: number;
    startChatWidth: number;
    startInspectorWidth: number;
  } | null>(null);

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

  useEffect(() => {
    try {
      window.localStorage.setItem(PANE_LAYOUT_STORAGE_KEY, `${Math.round(chatWidth)}:${Math.round(inspectorWidth)}`);
    } catch {
      // Pane sizing is a local preference only.
    }
  }, [chatWidth, inspectorWidth]);

  useEffect(() => {
    const media = window.matchMedia(DESKTOP_QUERY);
    const sync = () => setIsDesktop(media.matches);
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);

  useEffect(() => {
    const element = paneGridRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      setContainerWidth(Math.round(entry.contentRect.width));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const visibleSplitterCount = (chatOpen ? 1 : 0) + (inspectorOpen ? 1 : 0);
  const chatIsRail = chatOpen && focus === "graph";
  const measuredLayout = isDesktop && containerWidth > 0;
  const sidePaneBudget = measuredLayout
    ? Math.max(0, containerWidth - GRAPH_MIN_WIDTH - visibleSplitterCount * SPLITTER_WIDTH)
    : Number.POSITIVE_INFINITY;
  const chatFloor = chatIsRail ? CHAT_RAIL_WIDTH : CHAT_MIN_WIDTH;
  let effectiveChatWidth = chatOpen ? (chatIsRail ? CHAT_RAIL_WIDTH : chatWidth) : 0;
  let effectiveInspectorWidth = inspectorOpen ? inspectorWidth : 0;

  if (measuredLayout) {
    if (chatOpen && inspectorOpen) {
      if (sidePaneBudget >= chatFloor + INSPECTOR_MIN_WIDTH) {
        effectiveChatWidth = clamp(effectiveChatWidth, chatFloor, Math.min(CHAT_MAX_WIDTH, sidePaneBudget - INSPECTOR_MIN_WIDTH));
        effectiveInspectorWidth = clamp(
          inspectorWidth,
          INSPECTOR_MIN_WIDTH,
          Math.min(INSPECTOR_MAX_WIDTH, sidePaneBudget - effectiveChatWidth)
        );
      } else {
        effectiveChatWidth = Math.max(0, Math.min(chatFloor, Math.floor(sidePaneBudget * (chatIsRail ? 0.2 : 0.55))));
        effectiveInspectorWidth = Math.max(0, sidePaneBudget - effectiveChatWidth);
      }
    } else if (chatOpen) {
      effectiveChatWidth = clamp(effectiveChatWidth, chatFloor, Math.min(CHAT_MAX_WIDTH, Math.max(chatFloor, sidePaneBudget)));
    } else if (inspectorOpen) {
      effectiveInspectorWidth = clamp(
        inspectorWidth,
        INSPECTOR_MIN_WIDTH,
        Math.min(INSPECTOR_MAX_WIDTH, Math.max(INSPECTOR_MIN_WIDTH, sidePaneBudget))
      );
    }
  }

  const desktopGridTemplateColumns = useMemo(() => {
    if (!isDesktop) return undefined;
    if (chatOpen && inspectorOpen) {
      return `${effectiveChatWidth}px ${SPLITTER_WIDTH}px minmax(${GRAPH_MIN_WIDTH}px, 1fr) ${SPLITTER_WIDTH}px ${effectiveInspectorWidth}px`;
    }
    if (chatOpen) {
      return `${effectiveChatWidth}px ${SPLITTER_WIDTH}px minmax(${GRAPH_MIN_WIDTH}px, 1fr)`;
    }
    if (inspectorOpen) {
      return `minmax(${GRAPH_MIN_WIDTH}px, 1fr) ${SPLITTER_WIDTH}px ${effectiveInspectorWidth}px`;
    }
    return "minmax(0, 1fr)";
  }, [chatOpen, effectiveChatWidth, effectiveInspectorWidth, inspectorOpen, isDesktop]);

  const applyChatWidth = useCallback(
    (nextWidth: number) => {
      const maxWidth =
        containerWidth - GRAPH_MIN_WIDTH - (inspectorOpen ? effectiveInspectorWidth : 0) - visibleSplitterCount * SPLITTER_WIDTH;
      setPaneLayout((layout) => ({ ...layout, chatWidth: clamp(nextWidth, CHAT_MIN_WIDTH, Math.min(CHAT_MAX_WIDTH, Math.max(CHAT_MIN_WIDTH, maxWidth))) }));
    },
    [containerWidth, effectiveInspectorWidth, inspectorOpen, visibleSplitterCount]
  );

  const applyInspectorWidth = useCallback(
    (nextWidth: number) => {
      const maxWidth =
        containerWidth - GRAPH_MIN_WIDTH - (chatOpen ? effectiveChatWidth : 0) - visibleSplitterCount * SPLITTER_WIDTH;
      setPaneLayout((layout) => ({
        ...layout,
        inspectorWidth: clamp(nextWidth, INSPECTOR_MIN_WIDTH, Math.min(INSPECTOR_MAX_WIDTH, Math.max(INSPECTOR_MIN_WIDTH, maxWidth))),
      }));
    },
    [chatOpen, containerWidth, effectiveChatWidth, visibleSplitterCount]
  );

  const finishDrag = useCallback(() => {
    activeDragRef.current = null;
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  }, []);

  useEffect(() => {
    const move = (event: globalThis.PointerEvent) => {
      const drag = activeDragRef.current;
      if (!drag) return;
      const delta = event.clientX - drag.startX;
      if (drag.kind === "chat") applyChatWidth(drag.startChatWidth + delta);
      if (drag.kind === "inspector") applyInspectorWidth(drag.startInspectorWidth - delta);
    };
    const up = () => finishDrag();
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
    };
  }, [applyChatWidth, applyInspectorWidth, finishDrag]);

  const startDrag = (kind: "chat" | "inspector", event: PointerEvent<HTMLDivElement>) => {
    if (!isDesktop) return;
    if (kind === "chat" && focus === "graph") setFocus("balanced");
    activeDragRef.current = {
      kind,
      startX: event.clientX,
      startChatWidth: effectiveChatWidth || chatWidth,
      startInspectorWidth: effectiveInspectorWidth || inspectorWidth,
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    event.preventDefault();
  };

  const resizeWithKeyboard = (kind: "chat" | "inspector", event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const step = event.shiftKey ? 40 : 16;
    const direction = event.key === "ArrowRight" ? 1 : -1;
    if (kind === "chat") {
      if (focus === "graph") setFocus("balanced");
      applyChatWidth(effectiveChatWidth + direction * step);
    }
    if (kind === "inspector") applyInspectorWidth(effectiveInspectorWidth - direction * step);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-surface">
      <header className="flex min-h-[58px] flex-none items-center gap-3 border-b border-border px-4 py-2 max-[720px]:items-start max-[720px]:px-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 max-[720px]:flex-wrap">
            <div className="truncate text-[14px] font-semibold text-text max-[720px]:w-full">
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

        <div className="flex flex-none items-center gap-1.5">
          <Link
            href="/workspace?new=1"
            className="inline-flex items-center gap-1.5 rounded-[7px] border border-role-supervisor/45 bg-role-supervisor/10 px-3 py-2 text-[12px] font-semibold text-role-supervisor transition hover:bg-role-supervisor/15"
          >
            <PlusCircle className="h-3.5 w-3.5" />
            <span className="max-[720px]:hidden">New run</span>
          </Link>
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

      <div
        className={cn(
          "grid min-h-0 min-w-0 flex-1 overflow-hidden",
          timelineOpen ? "grid-rows-[minmax(0,1fr)_148px] xl:grid-rows-[minmax(0,1fr)_174px]" : "grid-rows-[minmax(0,1fr)_38px]"
        )}
      >
        <div
          ref={paneGridRef}
          style={desktopGridTemplateColumns ? { gridTemplateColumns: desktopGridTemplateColumns } : undefined}
          className={cn(
            "grid min-h-0 min-w-0 grid-rows-[minmax(0,1fr)] overflow-hidden max-[1279px]:grid-cols-1 max-[1279px]:grid-rows-[minmax(240px,0.9fr)_minmax(300px,1.1fr)]",
            chatOpen && inspectorOpen && focus === "balanced" && "grid-cols-[minmax(340px,0.82fr)_minmax(520px,1.45fr)_minmax(300px,344px)]",
            chatOpen && inspectorOpen && focus === "graph" && "grid-cols-[60px_minmax(520px,1fr)_minmax(300px,344px)]",
            chatOpen && !inspectorOpen && "grid-cols-[minmax(340px,0.62fr)_minmax(520px,1.5fr)]",
            !chatOpen && inspectorOpen && "grid-cols-[minmax(0,1fr)_minmax(300px,344px)]",
            !chatOpen && !inspectorOpen && "grid-cols-1",
            !chatOpen && "max-[1279px]:grid-rows-1"
          )}
        >
          {chatOpen && (
            <div className={cn("h-full min-h-0 border-r border-border max-[1279px]:order-2 max-[1279px]:border-r-0 max-[1279px]:border-t", focus === "graph" && "overflow-hidden")}>
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

          {chatOpen && (
            <PaneSplitter
              label="Resize chat and graph panes"
              onPointerDown={(event) => startDrag("chat", event)}
              onKeyDown={(event) => resizeWithKeyboard("chat", event)}
            />
          )}

          <main className="flex h-full min-h-0 min-w-0 flex-col max-[1279px]:order-1">
            <div className="flex h-[36px] items-center gap-2 border-b border-border bg-rail px-4">
              <GitBranch className="h-3.5 w-3.5 text-text-muted" />
              <span className="text-[12px] font-semibold text-text">Execution Flow</span>
              <span className="truncate text-[11px] text-text-faint">
                {mode === "live" ? "Grouped phases from live events and durable state" : "Grouped history reconstructed from stored run data"}
              </span>
            </div>
            <div className="min-h-0 flex-1">
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

          {inspectorOpen && (
            <PaneSplitter
              label="Resize graph and inspector panes"
              onPointerDown={(event) => startDrag("inspector", event)}
              onKeyDown={(event) => resizeWithKeyboard("inspector", event)}
            />
          )}

          {inspectorOpen && (
            <div className="h-full min-h-0 min-w-0 max-[1279px]:hidden">
              <NodeInspector
                model={model}
                selectedNode={selectedNode}
                open={inspectorOpen}
                deciding={deciding}
                onClose={() => setInspectorOpen(false)}
                onDecide={onDecideApproval}
              />
            </div>
          )}
        </div>

        <RunTimeline
          events={model.events}
          runStartedAt={model.task.created_at}
          selectedEventId={selectedEventId}
          selectedNodeId={selectedNodeId}
          collapsed={!timelineOpen}
          onSelectEvent={setSelectedEventId}
          onSelectNode={setSelectedNodeId}
          onToggle={() => setTimelineOpen((value) => !value)}
        />
      </div>
    </div>
  );
}
