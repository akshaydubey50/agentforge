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
      <h4 className="font-serif-display text-[16px] font-semibold tracking-tight text-text">{title}</h4>
      {subtitle && <span className="text-[12px] text-text-muted">- {subtitle}</span>}
      {actions && <div className="ml-auto flex items-center gap-2">{actions}</div>}
    </div>
  );
}
