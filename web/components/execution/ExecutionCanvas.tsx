"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  type ReactFlowInstance,
  type Edge,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Maximize2 } from "lucide-react";
import type { ExecutionEdge, ExecutionNode, ExecutionStatus, RunEvent } from "@/lib/execution/types";
import { cn } from "@/lib/utils";
import { ExecutionNodeView, type ExecutionFlowNode, type ExecutionNodeData } from "./ExecutionNodeView";

const nodeTypes = { executionNode: ExecutionNodeView };
const FIT_VIEW_OPTIONS = { padding: 0.2, minZoom: 0.18, maxZoom: 1.05 };
const DENSE_GRAPH_NODE_LIMIT = 24;
const DENSE_GRAPH_EDGE_LIMIT = 36;

const EDGE_COLOR: Record<ExecutionStatus, string> = {
  pending: "#4B5364",
  running: "#F5A623",
  waiting: "#64748B",
  approval_required: "#60A5FA",
  succeeded: "#34D399",
  failed: "#F87171",
  retrying: "#FB923C",
  blocked: "#F87171",
  skipped: "#4B5364",
  recovered: "#34D399",
  cancelled: "#64748B",
};

function toFlowNodes(nodes: ExecutionNode[], selectedNodeId: string | null, highlightedNodeIds: Set<string>): ExecutionFlowNode[] {
  return nodes.map((node) => ({
    id: node.id,
    type: "executionNode",
    position: node.position,
    data: node as ExecutionNodeData,
    draggable: false,
    selected: node.id === selectedNodeId,
    className: cn(highlightedNodeIds.has(node.id) && "ring-2 ring-role-human/70"),
  }));
}

function toFlowEdges(edges: ExecutionEdge[], selectedNodeId: string | null, animate: boolean): Edge[] {
  return edges.map((edge) => {
    const active = edge.source === selectedNodeId || edge.target === selectedNodeId || edge.status === "running";
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      animated: animate && (edge.status === "running" || edge.status === "retrying" || edge.status === "approval_required"),
      style: {
        stroke: EDGE_COLOR[edge.status ?? "pending"],
        strokeWidth: active ? 2.4 : 1.8,
        strokeDasharray:
          edge.relation === "requires_approval" || edge.relation === "retries" || edge.relation === "recovers" ? "5 4" : undefined,
      },
    };
  });
}

export function ExecutionCanvas({
  nodes,
  edges,
  events,
  selectedNodeId,
  selectedEventId,
  onSelectNode,
}: {
  nodes: ExecutionNode[];
  edges: ExecutionEdge[];
  events: RunEvent[];
  selectedNodeId: string | null;
  selectedEventId: string | null;
  onSelectNode: (nodeId: string | null) => void;
}) {
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance<ExecutionFlowNode, Edge> | null>(null);
  const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 });
  const [autoFollow, setAutoFollow] = useState(true);
  const denseGraph = nodes.length > DENSE_GRAPH_NODE_LIMIT || edges.length > DENSE_GRAPH_EDGE_LIMIT;

  const highlightedNodeIds = useMemo(() => {
    const event = events.find((item) => item.id === selectedEventId);
    return new Set(event?.nodeId ? [event.nodeId] : []);
  }, [events, selectedEventId]);

  const flowNodes = useMemo(() => toFlowNodes(nodes, selectedNodeId, highlightedNodeIds), [nodes, selectedNodeId, highlightedNodeIds]);
  const flowEdges = useMemo(() => toFlowEdges(edges, selectedNodeId, !denseGraph), [denseGraph, edges, selectedNodeId]);
  const graphLayoutKey = useMemo(
    () => nodes.map((node) => `${node.id}:${node.position.x}:${node.position.y}`).join("|"),
    [nodes]
  );

  const resetGraphView = useCallback(
    (duration = 180) => {
      if (!flowInstance || flowNodes.length === 0) return;
      setAutoFollow(true);
      window.requestAnimationFrame(() => {
        flowInstance.fitView({ ...FIT_VIEW_OPTIONS, duration });
      });
    },
    [flowInstance, flowNodes.length]
  );

  useEffect(() => {
    const element = wrapperRef.current;
    if (!element) return;

    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      const next = { width: Math.round(width), height: Math.round(height) };
      setViewportSize((current) => (current.width === next.width && current.height === next.height ? current : next));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!viewportSize.width || !viewportSize.height) return;
    if (!autoFollow) return;
    resetGraphView(120);
  }, [autoFollow, graphLayoutKey, resetGraphView, viewportSize.height, viewportSize.width]);

  const onNodeClick: NodeMouseHandler = (_, node) => onSelectNode(node.id);

  return (
    <div ref={wrapperRef} className="relative h-full min-h-[220px] min-w-0 bg-canvas sm:min-h-[280px]">
      <div className="pointer-events-none absolute right-3 top-3 z-10 flex items-center gap-2">
        <div className="rounded-[7px] border border-border bg-surface/90 px-2.5 py-1 text-[10.5px] uppercase tracking-[0.08em] text-text-faint shadow-sm">
          {nodes.length} phases
        </div>
        {!autoFollow && (
          <div className="rounded-[7px] border border-border bg-surface/90 px-2.5 py-1 text-[10.5px] uppercase tracking-[0.08em] text-text-faint shadow-sm">
            manual
          </div>
        )}
        <button
          type="button"
          onClick={() => resetGraphView()}
          disabled={!flowInstance || flowNodes.length === 0}
          className="pointer-events-auto inline-flex h-8 w-8 items-center justify-center rounded-[7px] border border-border bg-surface/90 text-text-muted shadow-sm transition hover:border-role-supervisor/60 hover:text-text disabled:cursor-not-allowed disabled:opacity-45"
          aria-label="Reset graph view"
          title="Reset graph view"
        >
          <Maximize2 className="h-3.5 w-3.5" />
        </button>
      </div>
      {nodes.length === 0 && (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center px-6 text-center">
          <div className="max-w-[360px] rounded-[8px] border border-border bg-surface/92 px-4 py-3 text-[12px] leading-relaxed text-text-muted">
            No graph nodes are available yet. Durable task data is loading; timeline events will appear as trace rows arrive.
          </div>
        </div>
      )}
      <ReactFlow<ExecutionFlowNode, Edge>
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={nodeTypes}
        onInit={(instance) => setFlowInstance(instance)}
        onNodeClick={onNodeClick}
        onMoveStart={(event) => {
          if (event) setAutoFollow(false);
        }}
        onPaneClick={() => onSelectNode(null)}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        fitView
        fitViewOptions={FIT_VIEW_OPTIONS}
        minZoom={0.18}
        maxZoom={1.45}
        zoomOnScroll
        zoomOnPinch
        panOnScroll
        panOnDrag
        preventScrolling
        onlyRenderVisibleElements
        colorMode="dark"
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={24} size={1} color="#1a2130" />
        <Controls showInteractive={false} fitViewOptions={FIT_VIEW_OPTIONS} className="!border-border !bg-surface !text-text" />
        {!denseGraph && (
          <MiniMap
            pannable
            zoomable
            nodeStrokeWidth={2}
            className="!border !border-border !bg-surface/90"
            maskColor="rgba(8, 12, 20, 0.58)"
          />
        )}
      </ReactFlow>
    </div>
  );
}
