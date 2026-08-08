export function SkeletonRows({ rows = 3 }: { rows?: number }) {
  return (
    <div className="rounded-[var(--rm)] border border-border bg-surface px-4 py-3.5">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className={`flex items-center gap-3 ${i === rows - 1 ? "" : "mb-3"}`}>
          <div className="skeleton-shimmer h-[38px] w-[38px] rounded-[10px]" />
          <div className="flex-1">
            <div className="skeleton-shimmer mb-1.5 h-[11px] w-[46%] rounded-md" />
            <div className="skeleton-shimmer h-[9px] w-[26%] rounded-md" />
          </div>
          <div className="skeleton-shimmer h-5 w-[62px] rounded-full" />
        </div>
      ))}
    </div>
  );
}
