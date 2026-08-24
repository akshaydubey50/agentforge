// Turns the compiled graph's nodes+edges into drawable coordinates.
//
// The point of computing this rather than hardcoding x/y is the same as the
// point of reading the topology off the compiled graph in the first place: a
// node added to graph/build.py should appear in the picture, correctly
// placed, without anyone touching the frontend. Hardcoded positions would
// reintroduce exactly the drift the topology endpoint exists to prevent.
//
// Layering is longest-path: a node sits one column right of its furthest-left
// predecessor. That is what puts `agent_step` after `sketch` even though
// START also points straight at it (the resume entry) -- shortest-path
// layering would pull it left and cross the diagram with a long edge.

import type { TopologyEdge, TopologyNode } from "./api";

export const NODE_W = 190;
export const NODE_H = 86;
export const TERMINAL_W = 104;
export const TERMINAL_H = 40;
const GAP_X = 78;
const GAP_Y = 34;
const PAD = 20;

export interface PlacedNode extends TopologyNode {
  x: number;
  y: number;
  w: number;
  h: number;
  layer: number;
}

export type EdgeShape = "forward" | "self" | "back";

export interface PlacedEdge extends TopologyEdge {
  shape: EdgeShape;
  /** SVG path `d`, already routed around the node boxes. */
  path: string;
}

export interface Layout {
  nodes: PlacedNode[];
  edges: PlacedEdge[];
  width: number;
  height: number;
}

/** Edges that would make the layering cyclic. Found by a DFS that marks
 *  nodes grey while they're on the current stack -- an edge into a grey node
 *  is a back edge, and gets excluded from layering and drawn as a return
 *  arc instead. Self-loops are handled separately and never reach here. */
function findBackEdges(nodes: TopologyNode[], edges: TopologyEdge[]): Set<string> {
  const out = new Map<string, string[]>();
  for (const n of nodes) out.set(n.id, []);
  for (const e of edges) {
    if (e.source === e.target) continue;
    out.get(e.source)?.push(e.target);
  }

  const back = new Set<string>();
  const state = new Map<string, 0 | 1 | 2>(); // unvisited / on-stack / done

  const visit = (id: string) => {
    state.set(id, 1);
    for (const next of out.get(id) ?? []) {
      const s = state.get(next) ?? 0;
      if (s === 1) back.add(`${id}->${next}`);
      else if (s === 0) visit(next);
    }
    state.set(id, 2);
  };

  // Seed from real entry points first so the DFS discovers the graph in its
  // natural direction; anything unreachable still gets visited afterwards.
  const hasIncoming = new Set(edges.filter((e) => e.source !== e.target).map((e) => e.target));
  for (const n of nodes) if (!hasIncoming.has(n.id) && (state.get(n.id) ?? 0) === 0) visit(n.id);
  for (const n of nodes) if ((state.get(n.id) ?? 0) === 0) visit(n.id);

  return back;
}

function assignLayers(nodes: TopologyNode[], edges: TopologyEdge[], back: Set<string>): Map<string, number> {
  const layer = new Map<string, number>();
  for (const n of nodes) layer.set(n.id, 0);

  const usable = edges.filter((e) => e.source !== e.target && !back.has(`${e.source}->${e.target}`));

  // Relax until stable. Bounded by node count: a longest path can't exceed
  // it, so this terminates even on input this function didn't expect.
  for (let pass = 0; pass < nodes.length; pass++) {
    let moved = false;
    for (const e of usable) {
      const want = (layer.get(e.source) ?? 0) + 1;
      if (want > (layer.get(e.target) ?? 0)) {
        layer.set(e.target, want);
        moved = true;
      }
    }
    if (!moved) break;
  }

  // Pull terminal exits to the far right so END never floats mid-diagram
  // when one branch is shorter than another.
  const maxLayer = Math.max(...layer.values());
  for (const n of nodes) {
    if (n.terminal && (layer.get(n.id) ?? 0) > 0) layer.set(n.id, maxLayer);
  }
  return layer;
}

function anchor(n: PlacedNode, side: "l" | "r" | "t" | "b") {
  switch (side) {
    case "l":
      return { x: n.x, y: n.y + n.h / 2 };
    case "r":
      return { x: n.x + n.w, y: n.y + n.h / 2 };
    case "t":
      return { x: n.x + n.w / 2, y: n.y };
    case "b":
      return { x: n.x + n.w / 2, y: n.y + n.h };
  }
}

export function layoutTopology(nodes: TopologyNode[], edges: TopologyEdge[]): Layout {
  const back = findBackEdges(nodes, edges);
  const layer = assignLayers(nodes, edges, back);

  const columns = new Map<number, TopologyNode[]>();
  for (const n of nodes) {
    const l = layer.get(n.id) ?? 0;
    if (!columns.has(l)) columns.set(l, []);
    columns.get(l)!.push(n);
  }

  const sizeOf = (n: TopologyNode) => ({
    w: n.terminal ? TERMINAL_W : NODE_W,
    h: n.terminal ? TERMINAL_H : NODE_H,
  });

  // Tallest column decides the canvas height; every other column is centred
  // against it, which keeps a single-node column (START) aligned with the
  // spine rather than pinned to the top.
  let tallest = 0;
  for (const [, group] of columns) {
    const h = group.reduce((sum, n) => sum + sizeOf(n).h, 0) + GAP_Y * (group.length - 1);
    tallest = Math.max(tallest, h);
  }

  const placed: PlacedNode[] = [];
  const sortedLayers = [...columns.keys()].sort((a, b) => a - b);
  let x = PAD;
  const colX = new Map<number, number>();

  for (const l of sortedLayers) {
    const group = columns.get(l)!;
    const colW = Math.max(...group.map((n) => sizeOf(n).w));
    colX.set(l, x);
    const colH = group.reduce((sum, n) => sum + sizeOf(n).h, 0) + GAP_Y * (group.length - 1);
    let y = PAD + (tallest - colH) / 2;
    for (const n of group) {
      const { w, h } = sizeOf(n);
      placed.push({ ...n, x: x + (colW - w) / 2, y, w, h, layer: l });
      y += h + GAP_Y;
    }
    x += colW + GAP_X;
  }

  const byId = new Map(placed.map((n) => [n.id, n]));
  const width = x - GAP_X + PAD;
  const height = tallest + PAD * 2;

  const placedEdges: PlacedEdge[] = edges.map((e) => {
    const a = byId.get(e.source)!;
    const b = byId.get(e.target)!;

    if (e.source === e.target) {
      // Self-loop: an arc off the top of the node and back into it. This is
      // the whole continuous-reasoning story, so it is drawn prominently
      // rather than as an afterthought.
      const top = anchor(a, "t");
      const lift = 40;
      const spread = 34;
      return {
        ...e,
        shape: "self" as const,
        path:
          `M ${top.x - spread} ${top.y} C ${top.x - spread} ${top.y - lift}, ` +
          `${top.x + spread} ${top.y - lift}, ${top.x + spread} ${top.y}`,
      };
    }

    if (back.has(`${e.source}->${e.target}`)) {
      const from = anchor(a, "b");
      const to = anchor(b, "b");
      const drop = 46;
      return {
        ...e,
        shape: "back" as const,
        path: `M ${from.x} ${from.y} C ${from.x} ${from.y + drop}, ${to.x} ${to.y + drop}, ${to.x} ${to.y}`,
      };
    }

    const from = anchor(a, "r");
    const to = anchor(b, "l");
    const bend = Math.max(28, (to.x - from.x) / 2);
    return {
      ...e,
      shape: "forward" as const,
      path: `M ${from.x} ${from.y} C ${from.x + bend} ${from.y}, ${to.x - bend} ${to.y}, ${to.x} ${to.y}`,
    };
  });

  return { nodes: placed, edges: placedEdges, width, height };
}
