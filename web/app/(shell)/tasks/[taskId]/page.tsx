import { redirect } from "next/navigation";

export default async function TaskCompatibilityPage({ params }: { params: Promise<{ taskId: string }> }) {
  const { taskId } = await params;
  redirect(`/workspace?run=${taskId}`);
}
