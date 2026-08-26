import { Suspense } from "react";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { WorkspacePageClient } from "./WorkspacePageClient";

export default function WorkspacePage() {
  return (
    <Suspense
      fallback={
        <div className="flex-1 p-5">
          <SkeletonRows rows={4} />
        </div>
      }
    >
      <WorkspacePageClient />
    </Suspense>
  );
}
