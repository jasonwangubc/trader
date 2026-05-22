import { api, ApiError } from "@/lib/api";
import { type WheelCandidate, type ScanStatus, type ConcentrationResponse } from "@/lib/wheel";
import { CandidatesView } from "./candidates-view";
import { ConcentrationPanel } from "./concentration-panel";
import { ScanControls } from "./scan-controls";

export const metadata = { title: "Wheel" };

export default async function WheelPage() {
  let candidates: WheelCandidate[] = [];
  let status: ScanStatus = { running: false };
  let concentration: ConcentrationResponse | null = null;
  let error: string | null = null;

  try {
    const [c, s, conc] = await Promise.all([
      api<WheelCandidate[]>("/api/wheel/candidates?limit=200"),
      api<ScanStatus>("/api/wheel/scan/status"),
      api<ConcentrationResponse>("/api/wheel/concentration"),
    ]);
    candidates = c;
    status = s;
    concentration = conc;
  } catch (e) {
    error = e instanceof ApiError ? `${e.status}: ${e.message}` : String(e);
  }

  return (
    <main className="container mx-auto max-w-[88rem] p-6 sm:p-8">
      <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Wheel</h1>
          <p className="text-muted-foreground mt-0.5 text-xs">
            Cash-secured puts and covered calls scored on yield, cushion, liquidity, and quality.
            Free yfinance options data · 5-min cached.
          </p>
        </div>
        <ScanControls initialStatus={status} hasCandidates={candidates.length > 0} />
      </header>

      {error && (
        <div className="border-destructive/50 bg-destructive/10 text-destructive mb-5 rounded-md border p-4 text-sm">
          {error}
        </div>
      )}

      <ConcentrationPanel data={concentration} />

      <CandidatesView candidates={candidates} />
    </main>
  );
}
