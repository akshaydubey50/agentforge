export function EmptyState({
  glyph,
  title,
  description,
  action,
}: {
  glyph: string;
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="rounded-[var(--rm)] border border-border bg-surface px-5 py-11 text-center">
      <div className="inline-block text-[32px] opacity-50">{glyph}</div>
      <h5 className="mb-1 mt-3 text-[16px] font-semibold text-text">{title}</h5>
      <p className="mx-auto mb-4 max-w-[42ch] text-[13px] text-text-muted">{description}</p>
      {action}
    </div>
  );
}
