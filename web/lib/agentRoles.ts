// Maps a TraceSpan.span_type to which of the three agent roles (plus the
// human-in-the-loop) "said" it, for the feed avatar/name/color treatment.
// span_type values are exactly agentsys' TraceSpan.span_type docstring:
// sketch, agent_step, tool_selection, tool_call, reasoning, review, memory,
// escalation, synthesize.

export type AgentRole = "supervisor" | "specialist" | "reviewer" | "human";

export const ROLE_META: Record<AgentRole, { label: string; initial: string; colorClass: string; bgClass: string }> = {
  supervisor: { label: "Supervisor", initial: "S", colorClass: "text-role-supervisor", bgClass: "bg-role-supervisor/15" },
  specialist: { label: "Specialist", initial: "E", colorClass: "text-role-specialist", bgClass: "bg-role-specialist/15" },
  reviewer: { label: "Reviewer", initial: "R", colorClass: "text-role-reviewer", bgClass: "bg-role-reviewer/15" },
  human: { label: "Human", initial: "H", colorClass: "text-role-human", bgClass: "bg-role-human/15" },
};

export function roleForSpanType(spanType: string): AgentRole {
  switch (spanType) {
    case "sketch":
    case "agent_step":
    case "synthesize":
      return "supervisor";
    case "tool_selection":
    case "tool_call":
    case "reasoning":
      return "specialist";
    case "review":
      return "reviewer";
    case "escalation":
      return "human";
    default:
      return "supervisor";
  }
}
