import { api } from "@/lib/api";
import { type HouseholdData } from "@/lib/tickets";

import { TicketForm } from "./ticket-form";

export default async function NewTicketPage() {
  const household = await api<HouseholdData>("/api/accounts");

  return (
    <main className="container mx-auto max-w-5xl p-6 sm:p-10">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight">New ticket</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Pre-commit setup, trigger, stop, target, and sizing — once armed, sizing and
          stop are immutable.
        </p>
      </header>

      <TicketForm accounts={household.accounts} />
    </main>
  );
}
