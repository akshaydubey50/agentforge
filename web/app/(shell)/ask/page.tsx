import { redirect } from "next/navigation";

export default async function AskCompatibilityPage({
  searchParams,
}: {
  searchParams: Promise<{ task?: string }>;
}) {
  const params = await searchParams;
  redirect(params.task ? `/workspace?run=${params.task}` : "/workspace");
}
