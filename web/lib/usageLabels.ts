// Maps cost.record_llm_call purposes (src/agentsys/graph/nodes.py) to the
// plain-language labels the approved design uses.
export const PURPOSE_LABEL: Record<string, string> = {
  plan: "Planning",
  tool_selection: "Choosing tools",
  review: "Checking answers",
  reasoning: "Model output",
  synthesize: "Writing the answer",
  subagent_step: "Helper agent steps",
};

export function purposeLabel(purpose: string): string {
  return PURPOSE_LABEL[purpose] ?? purpose;
}
