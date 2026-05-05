import { redirect } from "next/navigation";
import { api } from "@/lib/api";

export default async function SyncPage() {
  await api("/api/positions/sync", { method: "POST" });
  redirect("/positions");
}
