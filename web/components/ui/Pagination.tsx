import { Button } from "@/components/ui/button";

const PAGE_SIZES = [10, 25, 50, 100];

export function Pagination({
  offset,
  limit,
  total,
  onOffsetChange,
  onLimitChange,
}: {
  offset: number;
  limit: number;
  total: number;
  onOffsetChange: (offset: number) => void;
  onLimitChange: (limit: number) => void;
}) {
  if (total === 0) return null;

  const page = Math.floor(offset / limit) + 1;
  const pageCount = Math.max(Math.ceil(total / limit), 1);
  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = Math.min(offset + limit, total);
  const atFirst = offset <= 0;
  const atLast = offset + limit >= total;

  const goFirst = () => onOffsetChange(0);
  const goPrev = () => onOffsetChange(Math.max(offset - limit, 0));
  const goNext = () => onOffsetChange(Math.min(offset + limit, (pageCount - 1) * limit));
  const goLast = () => onOffsetChange((pageCount - 1) * limit);

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2.5 text-[12.5px] text-text-muted">
      <span>
        {rangeStart}–{rangeEnd} of {total}
      </span>

      <div className="ml-auto flex gap-1">
        <Button variant="outline" size="sm" onClick={goFirst} disabled={atFirst} title="First page">
          « First
        </Button>
        <Button variant="outline" size="sm" onClick={goPrev} disabled={atFirst} title="Previous page">
          ‹ Prev
        </Button>
        <span className="px-2 py-1">
          Page {page} of {pageCount}
        </span>
        <Button variant="outline" size="sm" onClick={goNext} disabled={atLast} title="Next page">
          Next ›
        </Button>
        <Button variant="outline" size="sm" onClick={goLast} disabled={atLast} title="Last page">
          Last »
        </Button>
      </div>

      <label className="flex items-center gap-1.5">
        Per page
        <select
          className="rounded-[var(--rs)] border border-border-strong bg-background px-2 py-1 text-[12px] text-text"
          value={limit}
          onChange={(e) => onLimitChange(Number(e.target.value))}
        >
          {PAGE_SIZES.map((size) => (
            <option key={size} value={size}>
              {size}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
