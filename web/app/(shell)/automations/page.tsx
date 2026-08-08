import { TopBar } from "@/components/shell/TopBar";
import { EmptyState } from "@/components/ui/EmptyState";

export default function AutomationsPage() {
  return (
    <>
      <TopBar title="Automations" subtitle="things that run on their own" />
      <div className="flex-1 overflow-y-auto px-5 py-5">
        <EmptyState
          glyph="↻"
          title="Not built yet"
          description="There's no scheduling concept in the backend yet — this screen is a preview of where recurring tasks like a weekly digest will live."
        />
      </div>
    </>
  );
}
