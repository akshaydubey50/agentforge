"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";

// Height-animates via grid-template-rows (0fr <-> 1fr) instead of measuring
// pixel heights in JS -- smoother and correct even when content reflows,
// same easing curve as the rest of the app's motion (globals.css'
// dialog-in/menu-in), so this reads as one consistent motion language
// rather than a one-off.
export function Collapsible({
  label,
  defaultOpen = false,
  className,
  children,
}: {
  label: React.ReactNode;
  defaultOpen?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={className}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-1.5 text-[11px] font-medium text-text-faint transition-colors hover:text-text-muted"
      >
        <svg
          viewBox="0 0 8 8"
          fill="none"
          className={cn("h-[7px] w-[7px] flex-none transition-transform duration-200 ease-out", open && "rotate-90")}
        >
          <path d="M2 1L6 4L2 7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        {label}
      </button>
      <div
        className="grid transition-[grid-template-rows] duration-300 ease-[cubic-bezier(0.2,0.8,0.2,1)]"
        style={{ gridTemplateRows: open ? "1fr" : "0fr" }}
      >
        <div className="overflow-hidden">
          <div className="pt-1.5">{children}</div>
        </div>
      </div>
    </div>
  );
}
