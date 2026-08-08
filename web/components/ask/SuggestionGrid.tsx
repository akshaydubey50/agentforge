const SUGGESTIONS = [
  { k: "Look up", q: "How did revenue change last quarter?" },
  { k: "Read the web", q: "Summarise a competitor's pricing page" },
  { k: "Calculate", q: "What's 8.85% growth on $9,600,000?" },
  { k: "Write", q: "Draft a one-page update for my team" },
];

export function SuggestionGrid({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="mt-4 grid grid-cols-2 gap-2.5">
      {SUGGESTIONS.map((s) => (
        <button
          key={s.q}
          onClick={() => onPick(s.q)}
          className="rounded-[var(--rm)] border border-border bg-surface px-3.5 py-3 text-left transition-colors hover:border-brand"
        >
          <div className="text-[10px] font-extrabold uppercase tracking-wider text-brand">{s.k}</div>
          <div className="mt-1 text-[13px] text-text">{s.q}</div>
        </button>
      ))}
    </div>
  );
}
