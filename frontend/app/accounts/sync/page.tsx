import { redirect } from "next/navigation";
import { api } from "@/lib/api";

/**
 * Hitting /accounts/sync triggers a fresh broker pull then redirects back.
 * Keeping this as a dedicated route means we don't need a client-side fetch
 * or a Server Action for the simple "refresh" case.
 */
export default async function SyncPage() {
  await api("/api/accounts/sync", { method: "GET" });
  redirect("/accounts");
}
