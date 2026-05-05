import { redirect } from "next/navigation";
import { api } from "@/lib/api";

export default async function SyncPage({
  searchParams,
}: {
  searchParams: Promise<{ mode?: string }>;
}) {
  const { mode = "auto" } = await searchParams;
  await api(`/api/screener/sync?mode=${mode}`, { method: "POST" });
  redirect("/screener");
}
