import type { ReactNode } from "react";

export function TopBar({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-none items-center gap-3 border-b border-border px-5 py-3.5">
      <div className="min-w-0 flex-1">
        <h4 className="truncate font-serif-display text-[16px] font-semibold tracking-tight text-text">{title}</h4>
        {subtitle && <div className="mt-0.5 truncate text-[12px] text-text-muted">{subtitle}</div>}
      </div>
      {actions && <div className="flex flex-none items-center gap-2">{actions}</div>}
    </div>
  );
}
