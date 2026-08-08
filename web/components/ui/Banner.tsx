import { cn } from "@/lib/utils";

export function Banner({
  kind,
  title,
  children,
}: {
  kind: "e" | "w";
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "animate-settle-in mb-2.5 flex gap-2.5 rounded-[var(--rm)] p-3.5",
        kind === "e" ? "bg-bad-wash" : "bg-warn-wash"
      )}
    >
      <span className="text-[16px]">{kind === "e" ? "⚠" : "◔"}</span>
      <div>
        <b className={cn("block text-[13px]", kind === "e" ? "text-bad" : "text-warn")}>{title}</b>
        <div className="mt-0.5 text-[12.5px] text-text-muted [&_a]:font-semibold [&_a]:text-brand [&_a]:underline">
          {children}
        </div>
      </div>
    </div>
  );
}
