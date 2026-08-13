"use client";

import { useMemo, useState } from "react";
import { ReactFlow, Background, BackgroundVariant, type Edge, type NodeMouseHandler } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { TaskDetailOut, TraceSpanOut, EscalationOut } from "@/lib/api";
import { buildGraph, type GraphNodeData, type StatusTone } from "@/lib/agentGraph";
import { AgentNode, type AgentFlowNode } from "./AgentNode";
import { GraphInspector } from "./GraphInspector";

const nodeTypes = { agentNode: AgentNode };

const TONE_STROKE: Record<StatusTone, string> = {
  done: "#34D399",
  active: "#F5A623",
  esc: "#60A5FA",
  failed: "#F87171",
  pending: "#4B5364",
};

export function AgentGraphView({ task, spans, escalations }: { task: TaskDetailOut; spans: TraceSpanOut[]; escalations: EscalationOut[] }) {
  const { nodes: graphNodes, edges: graphEdges } = useMemo(() => buildGraph(task, spans, escalations), [task, spans, escalations]);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const rfNodes: AgentFlowNode[] = graphNodes.map((n) => ({
    id: n.id,
    type: "agentNode",
    position: { x: n.x, y: n.y },
    data: n,
    draggable: false,
    selected: n.id === selectedId,
  }));

  const rfEdges: Edge[] = graphEdges.map((e) => ({
    id: e.id,
    source: e.from,
    target: e.to,
    animated: e.tone === "active",
    style: { stroke: TONE_STROKE[e.tone], strokeWidth: 2, strokeDasharray: e.dashed ? "5 4" : undefined },
  }));

  const onNodeClick: NodeMouseHandler = (_, node) => setSelectedId(node.id);

  const selected: GraphNodeData | null = graphNodes.find((n) => n.id === selectedId) ?? null;

  return (
    <div className="flex min-h-0 flex-1">
      <div className="min-w-0 flex-1 bg-canvas">
        <ReactFlow
          nodes={rfNodes}
          edges={rfEdges}
          nodeTypes={nodeTypes}
          onNodeClick={onNodeClick}
          onPaneClick={() => setSelectedId(null)}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable
          fitView
          fitViewOptions={{ padding: 0.15 }}
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={24} size={1} color="#1a2130" />
        </ReactFlow>
      </div>
      <GraphInspector node={selected} task={task} />
    </div>
  );
}
