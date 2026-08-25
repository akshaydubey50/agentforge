import { redirect } from "next/navigation";

export default async function TaskGraphCompatibilityPage({ params }: { params: Promise<{ taskId: string }> }) {
  const { taskId } = await params;
  redirect(`/workspace?run=${taskId}`);
}
