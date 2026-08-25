"use client";

import { useMemo } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  type Edge,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { ExecutionEdge, ExecutionNode, ExecutionStatus, RunEvent } from "@/lib/execution/types";
import { cn } from "@/lib/utils";
import { ExecutionNodeView, type ExecutionFlowNode, type ExecutionNodeData } from "./ExecutionNodeView";

const nodeTypes = { executionNode: ExecutionNodeView };

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

function toFlowEdges(edges: ExecutionEdge[], selectedNodeId: string | null): Edge[] {
  return edges.map((edge) => {
    const active = edge.source === selectedNodeId || edge.target === selectedNodeId || edge.status === "running";
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      animated: edge.status === "running" || edge.status === "retrying" || edge.status === "approval_required",
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
  const highlightedNodeIds = useMemo(() => {
    const event = events.find((item) => item.id === selectedEventId);
    return new Set(event?.nodeId ? [event.nodeId] : []);
  }, [events, selectedEventId]);

  const flowNodes = useMemo(() => toFlowNodes(nodes, selectedNodeId, highlightedNodeIds), [nodes, selectedNodeId, highlightedNodeIds]);
  const flowEdges = useMemo(() => toFlowEdges(edges, selectedNodeId), [edges, selectedNodeId]);

  const onNodeClick: NodeMouseHandler = (_, node) => onSelectNode(node.id);

  return (
    <div className="relative h-full min-h-[360px] bg-canvas">
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={nodeTypes}
        onNodeClick={onNodeClick}
        onPaneClick={() => onSelectNode(null)}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        fitView
        fitViewOptions={{ padding: 0.18 }}
        minZoom={0.35}
        maxZoom={1.45}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={24} size={1} color="#1a2130" />
        <Controls showInteractive={false} className="!border-border !bg-surface !text-text" />
        <MiniMap
          pannable
          zoomable
          nodeStrokeWidth={2}
          className="!border !border-border !bg-surface/90"
          maskColor="rgba(8, 12, 20, 0.58)"
        />
      </ReactFlow>
    </div>
  );
}
