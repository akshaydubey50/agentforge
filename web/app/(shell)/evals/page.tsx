"use client";

import { CheckCircle2, FlaskConical, Gauge, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";
import { TopBar } from "@/components/shell/TopBar";

const DETERMINISTIC = [
  { name: "Policy tests", type: "pytest safety", status: "Run from backend test suite", signal: "pass/fail" },
  { name: "Approval fingerprint tests", type: "execution safety", status: "Run from backend test suite", signal: "pass/fail" },
  { name: "Owner isolation tests", type: "security", status: "Run from backend test suite", signal: "pass/fail" },
  { name: "Graph/event model adapter", type: "frontend unit", status: "Phase 9B test target", signal: "pass/fail" },
];

const QUALITY = [
  { name: "RAG citation quality", type: "retrieval/generation", status: "No frontend results API exposed", signal: "score with threshold" },
  { name: "Answer relevance", type: "DeepEval optional", status: "Not a runtime safety gate", signal: "probabilistic score" },
  { name: "Context compression quality", type: "model quality", status: "Future dataset/report API", signal: "trend" },
];

export default function EvalsPage() {
  return (
    <>
      <TopBar title="Evals" subtitle="deterministic safety is separate from probabilistic quality" />
      <div className="flex-1 overflow-y-auto px-7 py-5.5">
        <div className="grid grid-cols-2 gap-5 max-[1000px]:grid-cols-1">
          <EvalSection
            title="Deterministic Safety"
            icon={<ShieldCheck className="h-4 w-4" />}
            description="Binary checks for policy, approvals, execution safety, guardrails, and ownership. These are production gates."
            rows={DETERMINISTIC}
            tone="safety"
          />
          <EvalSection
            title="Probabilistic Quality"
            icon={<Gauge className="h-4 w-4" />}
            description="Judge/model or retrieval metrics that help improve answer quality. They are not equivalent to deterministic safety."
            rows={QUALITY}
            tone="quality"
          />
        </div>

        <section className="mt-5 rounded-[8px] border border-border bg-rail p-4 text-[12.5px] leading-relaxed text-text-muted">
          No eval-results API is exposed to the production frontend yet. Phase 9B should add a read-only eval run/dataset endpoint before this page shows historical scores.
        </section>
      </div>
    </>
  );
}

function EvalSection({
  title,
  icon,
  description,
  rows,
  tone,
}: {
  title: string;
  icon: ReactNode;
  description: string;
  rows: { name: string; type: string; status: string; signal: string }[];
  tone: "safety" | "quality";
}) {
  return (
    <section className="overflow-hidden rounded-[8px] border border-border bg-surface">
      <div className="border-b border-border px-4 py-3">
        <div className="flex items-center gap-2 text-text">
          {icon}
          <h2 className="text-[13.5px] font-semibold">{title}</h2>
          <span className={tone === "safety" ? "ml-auto text-status-completed" : "ml-auto text-role-supervisor"}>
            {tone === "safety" ? <CheckCircle2 className="h-4 w-4" /> : <FlaskConical className="h-4 w-4" />}
          </span>
        </div>
        <p className="mt-1 text-[12px] leading-relaxed text-text-muted">{description}</p>
      </div>
      <div className="divide-y divide-border">
        {rows.map((row) => (
          <div key={row.name} className="grid grid-cols-[1fr_0.7fr] gap-3 px-4 py-3 text-[12px] max-[720px]:grid-cols-1">
            <div>
              <div className="font-semibold text-text">{row.name}</div>
              <div className="mt-0.5 text-text-faint">{row.type}</div>
            </div>
            <div>
              <div className="text-text-muted">{row.status}</div>
              <div className="mt-0.5 font-mono text-[11px] text-text-faint">{row.signal}</div>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
