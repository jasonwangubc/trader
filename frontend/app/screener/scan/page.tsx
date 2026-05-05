import { redirect } from "next/navigation";
import { api } from "@/lib/api";

export default async function ScanPage() {
  await api("/api/screener/scan", { method: "POST" });
  redirect("/screener");
}
