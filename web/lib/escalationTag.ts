// Escalation.reason (src/agentsys/db/models.py) is free text, not a
// structured enum -- there's no backend "reason_type" field to read. The
// four call sites in graph/nodes.py each write a recognizable, stable
// prefix, so this derives the mockups' reviewer_flag/low_confidence-style
// tag from the real reason string instead of inventing a field the API
// doesn't expose.

export type EscalationLevel = "plan" | "subtask";

export interface EscalationClassification {
  tag: string;
  level: EscalationLevel;
}

export function classifyEscalation(reason: string, subtaskId: string | null): EscalationClassification {
  if (reason.startsWith("Low sketch confidence")) return { tag: "low_confidence", level: "plan" };
  if (reason.startsWith("Reviewer verdict")) return { tag: "reviewer_flag", level: "subtask" };
  if (reason.startsWith("Exceeded max_task_steps")) return { tag: "step_budget", level: "plan" };
  if (reason.startsWith("Agent chose to finish")) return { tag: "premature_finish", level: "plan" };
  return { tag: subtaskId ? "subtask_flag" : "plan_flag", level: subtaskId ? "subtask" : "plan" };
}
