"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { API_URL } from "@/lib/api";
import {
  type Account,
  type GuardrailWarning,
  type SizingPreview,
  type StreakSnapshot,
  type TicketPreviewOut,
  SETUP_TYPES,
  TRIGGER_TYPES,
  fmtMoney,
  fmtPct,
} from "@/lib/tickets";

type FormState = {
  account_id: string;
  symbol: string;
  currency: "CAD" | "USD";
  setup_type: string;
  trigger_type: string;
  trigger_price: string;
  stop_price: string;
  target_price: string;
  time_stop_days: string;
  valid_for_days: string;
  volume_confirm_multiple: string;
  thesis: string;
};

export function TicketForm({
  accounts,
  prefillSymbol,
  prefillTrigger,
  prefillStop,
  prefillTarget,
}: {
  accounts: Account[];
  prefillSymbol?: string;
  prefillTrigger?: string;
  prefillStop?: string;
  prefillTarget?: string;
}) {
  const router = useRouter();
  const [form, setForm] = useState<FormState>(() => ({
    account_id: accounts[0]?.id ?? "",
    symbol: prefillSymbol ?? "",
    currency: "USD",
    setup_type: "VCP",
    trigger_type: "price_above_with_volume",
    trigger_price: prefillTrigger ?? "",
    stop_price: prefillStop ?? "",
    target_price: prefillTarget ?? "",
    time_stop_days: "21",
    valid_for_days: "7",
    volume_confirm_multiple: "1.5",
    thesis: "",
  }));
  const [preview, setPreview] = useState<TicketPreviewOut | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [guardrailBlock, setGuardrailBlock] = useState<{ code: string; message: string } | null>(null);
  const [overrideRegime, setOverrideRegime] = useState(false);
  const [overrideStreak, setOverrideStreak] = useState(false);
  const [isPaper, setIsPaper] = useState<boolean>(false);
  const [earningsWarning, setEarningsWarning] = useState<string | null>(null);
  const [suggest, setSuggest] = useState<{
    trigger_price: number | null; stop_price: number | null;
    target_price: number | null; stop_method: string;
    setup_type: string | null; atr14: number | null;
  } | null>(null);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((s) => ({ ...s, [key]: value }));

  // Fetch earnings warning when symbol changes
  useEffect(() => {
    const sym = form.symbol.trim().toUpperCase();
    if (!sym || sym.length < 1) { setEarningsWarning(null); return; }
    const ctrl = new AbortController();
    const t = setTimeout(async () => {
      try {
        const res = await fetch(`${API_URL}/api/earnings/${sym}`, { signal: ctrl.signal });
        if (res.ok) {
          const d = await res.json();
          setEarningsWarning(d.warning ?? null);
        } else {
          setEarningsWarning(null);
        }
      } catch { setEarningsWarning(null); }
    }, 600);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [form.symbol]);

  // Auto-populate prices from screener suggestion when symbol changes.
  useEffect(() => {
    const sym = form.symbol.trim().toUpperCase();
    if (sym.length < 1) { setSuggest(null); return; }
    const ctrl = new AbortController();
    const t = setTimeout(async () => {
      try {
        const res = await fetch(`${API_URL}/api/screener/suggest/${sym}`, { signal: ctrl.signal });
        if (!res.ok) { setSuggest(null); return; }
        const s = await res.json();
        setSuggest(s);
        setForm(prev => ({
          ...prev,
          trigger_price: s.trigger_price != null ? String(s.trigger_price) : prev.trigger_price,
          stop_price:    s.stop_price    != null ? String(s.stop_price)    : prev.stop_price,
          target_price:  s.target_price  != null ? String(s.target_price)  : prev.target_price,
          ...(s.setup_type ? { setup_type: s.setup_type } : {}),
        }));
      } catch { setSuggest(null); }
    }, 600);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [form.symbol]);

  // Live sizing preview — debounced.
  const previewKey = `${form.account_id}|${form.currency}|${form.trigger_price}|${form.stop_price}`;
  useEffect(() => {
    const trigger = parseFloat(form.trigger_price);
    const stop = parseFloat(form.stop_price);
    if (!form.account_id || !Number.isFinite(trigger) || !Number.isFinite(stop) || trigger <= 0 || stop <= 0) {
      setPreview(null);
      setPreviewError(null);
      return;
    }
    const ctrl = new AbortController();
    const t = setTimeout(async () => {
      try {
        const res = await fetch(`${API_URL}/api/tickets/preview`, {
          method: "POST",
          signal: ctrl.signal,
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            account_id: form.account_id,
            currency: form.currency,
            trigger_price: trigger,
            stop_price: stop,
          }),
        });
        if (!res.ok) {
          setPreview(null);
          setPreviewError(`${res.status}: ${await res.text()}`);
          return;
        }
        setPreview(await res.json());
        setPreviewError(null);
      } catch (e) {
        if ((e as Error).name === "AbortError") return;
        setPreviewError((e as Error).message);
      }
    }, 250);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewKey]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitError(null);
    setSubmitting(true);
    try {
      const body = {
        account_id: form.account_id,
        symbol: form.symbol,
        currency: form.currency,
        setup_type: form.setup_type,
        trigger_type: form.trigger_type,
        trigger_price: parseFloat(form.trigger_price),
        stop_price: parseFloat(form.stop_price),
        target_price: form.target_price ? parseFloat(form.target_price) : null,
        time_stop_days: form.time_stop_days ? parseInt(form.time_stop_days, 10) : null,
        valid_for_days: parseInt(form.valid_for_days, 10),
        volume_confirm_multiple: form.volume_confirm_multiple
          ? parseFloat(form.volume_confirm_multiple)
          : null,
        thesis: form.thesis,
      };
      const res = await fetch(`${API_URL}/api/tickets`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...body, is_paper: isPaper, override_regime: overrideRegime, override_streak: overrideStreak }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        // Guardrail violation (422)
        if (res.status === 422 && data?.detail?.code) {
          setGuardrailBlock(data.detail);
          setSubmitting(false);
          return;
        }
        throw new Error(`${res.status}: ${JSON.stringify(data ?? await res.text())}`);
      }
      setGuardrailBlock(null);
      router.push("/tickets");
      router.refresh();
    } catch (e) {
      setSubmitError((e as Error).message);
      setSubmitting(false);
    }
  };

  const ticketReward = useMemo(() => {
    const t = parseFloat(form.trigger_price);
    const s = parseFloat(form.stop_price);
    const target = parseFloat(form.target_price);
    if (!Number.isFinite(t) || !Number.isFinite(s) || !Number.isFinite(target) || t - s <= 0) return null;
    return ((target - t) / (t - s)).toFixed(2);
  }, [form.trigger_price, form.stop_price, form.target_price]);

  const thesisLen = form.thesis.trim().length;
  const missingReasons: string[] = [];
  if (!form.symbol.trim()) missingReasons.push("symbol required");
  if (thesisLen < 10) missingReasons.push(`thesis too short (${thesisLen}/10 chars)`);
  if (!preview) missingReasons.push("enter trigger and stop prices");
  if (preview && preview.sizing.shares === 0) missingReasons.push("sizing returns 0 shares — sync accounts");

  const canSubmit =
    !!form.account_id &&
    form.symbol.trim().length > 0 &&
    thesisLen >= 10 &&
    preview !== null &&
    preview.sizing.shares > 0 &&
    !submitting;

  const regimeColor =
    preview?.regime === "bull"    ? "text-emerald-600 dark:text-emerald-400" :
    preview?.regime === "caution" ? "text-amber-600 dark:text-amber-400" :
    preview?.regime === "bear"    ? "text-destructive" : "";

  const selectedAccount = accounts.find(a => a.id === form.account_id);

  return (
    <form onSubmit={submit} className="grid gap-6 lg:grid-cols-[1fr_22rem]">
      <div className="space-y-6">
        {/* Paper trade toggle */}
        <div className={`rounded-lg border px-4 py-3 flex items-center justify-between gap-4 ${isPaper ? "border-muted bg-muted/30" : "border-emerald-500/30 bg-emerald-500/5"}`}>
          <div>
            <p className="text-sm font-medium">
              {isPaper ? "Paper trade — simulated only, no real orders sent" : "Live trade — real orders will go to Questrade"}
            </p>
            <p className="text-muted-foreground text-xs mt-0.5">
              {isPaper ? "Use this for testing setups without risk." : "Make sure your account has real-money execution enabled."}
            </p>
          </div>
          <label className="flex items-center gap-2 cursor-pointer shrink-0">
            <span className="text-xs text-muted-foreground">Paper</span>
            <input
              type="checkbox"
              checked={isPaper}
              onChange={e => setIsPaper(e.target.checked)}
              className="h-4 w-4 rounded border-border accent-primary cursor-pointer"
            />
          </label>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Setup</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <Field label="Account">
              <select
                value={form.account_id}
                onChange={(e) => {
                  const acc = accounts.find((a) => a.id === e.target.value);
                  set("account_id", e.target.value);
                  if (acc) set("currency", acc.primary_currency as "CAD" | "USD");
                }}
                className="border-input bg-background h-9 rounded-md border px-3 text-sm"
              >
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.type} · #{a.questrade_account_id}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Currency">
              <select
                value={form.currency}
                onChange={(e) => set("currency", e.target.value as "CAD" | "USD")}
                className="border-input bg-background h-9 rounded-md border px-3 text-sm"
              >
                <option value="USD">USD</option>
                <option value="CAD">CAD</option>
              </select>
            </Field>
            <Field label="Symbol">
              <Input
                value={form.symbol}
                onChange={(e) => set("symbol", e.target.value.toUpperCase())}
                placeholder="AAPL"
                className="font-mono uppercase"
                required
              />
            </Field>
            <Field label="Setup type">
              <select
                value={form.setup_type}
                onChange={(e) => set("setup_type", e.target.value)}
                className="border-input bg-background h-9 rounded-md border px-3 text-sm"
              >
                {SETUP_TYPES.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </Field>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Trigger</CardTitle>
            <CardDescription>How and when the entry order fires.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <Field label="Trigger type" className="sm:col-span-2">
              <select
                value={form.trigger_type}
                onChange={(e) => set("trigger_type", e.target.value)}
                className="border-input bg-background h-9 rounded-md border px-3 text-sm"
              >
                {TRIGGER_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Trigger price">
              <Input
                type="number"
                step="0.01"
                min="0.01"
                value={form.trigger_price}
                onChange={(e) => set("trigger_price", e.target.value)}
                required
                className="tabular-nums"
              />
            </Field>
            {form.trigger_type === "price_above_with_volume" && (
              <Field label="Volume confirm (× avg)">
                <Input
                  type="number"
                  step="0.1"
                  min="1.0"
                  value={form.volume_confirm_multiple}
                  onChange={(e) => set("volume_confirm_multiple", e.target.value)}
                  className="tabular-nums"
                />
              </Field>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Risk management</CardTitle>
            <CardDescription>
              Stop is immutable once armed.
              {suggest && (
                <span className="ml-2 text-primary/80">
                  · Screener: pivot ${suggest.trigger_price?.toFixed(2)},
                  stop ${suggest.stop_price?.toFixed(2)} ({suggest.stop_method}
                  {suggest.atr14 ? `, ATR ${suggest.atr14.toFixed(2)}` : ""}),
                  target ${suggest.target_price?.toFixed(2)} (3R)
                </span>
              )}
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-3">
            <Field label="Stop price">
              <Input
                type="number"
                step="0.01"
                min="0.01"
                value={form.stop_price}
                onChange={(e) => set("stop_price", e.target.value)}
                required
                className="tabular-nums"
              />
            </Field>
            <Field label={`Target price${ticketReward ? ` (${ticketReward}R)` : ""}`}>
              <Input
                type="number"
                step="0.01"
                min="0.01"
                value={form.target_price}
                onChange={(e) => set("target_price", e.target.value)}
                className="tabular-nums"
              />
            </Field>
            <Field label="Time stop (days)">
              <Input
                type="number"
                min="1"
                max="365"
                value={form.time_stop_days}
                onChange={(e) => set("time_stop_days", e.target.value)}
                className="tabular-nums"
              />
            </Field>
            <Field label="Valid for (days)">
              <Input
                type="number"
                min="1"
                max="90"
                value={form.valid_for_days}
                onChange={(e) => set("valid_for_days", e.target.value)}
                className="tabular-nums"
              />
            </Field>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Thesis</CardTitle>
            <CardDescription>One paragraph. The why, not the how.</CardDescription>
          </CardHeader>
          <CardContent>
            <Textarea
              value={form.thesis}
              onChange={(e) => set("thesis", e.target.value)}
              placeholder="e.g. AAPL forming tight 8wk VCP, RS 92, earnings beat last quarter..."
              rows={4}
              required
              minLength={10}
            />
          </CardContent>
        </Card>

        {submitError && (
          <div className="border-destructive/50 bg-destructive/10 text-destructive rounded-md border p-4 text-sm">
            {submitError}
          </div>
        )}

        {/* Earnings warning */}
        {earningsWarning && (
          <div className="border-amber-400 bg-amber-50 dark:bg-amber-950/20 text-amber-800 dark:text-amber-300 rounded-md border px-3 py-2 text-xs font-medium">
            📅 {earningsWarning}
          </div>
        )}

        {/* Regime indicator */}
        {preview?.regime && (
          <div className={`text-xs ${regimeColor}`}>
            Market regime: <span className="font-semibold uppercase">{preview.regime}</span>
            {preview.regime === "bear" && " — new tickets not recommended"}
          </div>
        )}

        {/* Guardrail warnings from preview */}
        {preview?.guardrail_warnings && preview.guardrail_warnings.length > 0 && (
          <div className="space-y-1">
            {preview.guardrail_warnings.map((w) => (
              <div key={w.code} className="text-amber-600 dark:text-amber-400 text-xs border border-amber-200 dark:border-amber-800 rounded-md px-3 py-2">
                ⚠ {w.message}
              </div>
            ))}
          </div>
        )}

        {/* Hard guardrail block */}
        {guardrailBlock && (
          <div className="border-destructive/50 bg-destructive/10 rounded-md border p-4 text-sm space-y-3">
            <p className="text-destructive font-medium">Guardrail blocked: {guardrailBlock.code.replace(/_/g, " ")}</p>
            <p className="text-sm">{guardrailBlock.message}</p>
            {(guardrailBlock.code === "regime_bear" || guardrailBlock.code === "regime_caution") && (
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={overrideRegime} onChange={e => setOverrideRegime(e.target.checked)} />
                I understand the regime risk — override and arm anyway
              </label>
            )}
            {guardrailBlock.code === "loss_streak" && (
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={overrideStreak} onChange={e => setOverrideStreak(e.target.checked)} />
                I have reviewed my recent trades — override loss-streak block
              </label>
            )}
          </div>
        )}

        {/* Why button is disabled */}
        {!canSubmit && !submitting && missingReasons.length > 0 && (
          <p className="text-muted-foreground text-xs">
            To arm: {missingReasons.join(" · ")}
          </p>
        )}

        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={() => router.push("/tickets")}
            className="border-input hover:bg-accent inline-flex h-10 items-center rounded-md border px-5 text-sm font-medium"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!canSubmit && !(guardrailBlock && (overrideRegime || overrideStreak))}
            className="bg-primary text-primary-foreground inline-flex h-10 items-center rounded-md px-5 text-sm font-medium transition-colors hover:bg-primary/90 disabled:opacity-50"
          >
            {submitting ? "Arming…" : "Arm ticket"}
          </button>
        </div>
      </div>

      <aside className="lg:sticky lg:top-6 lg:self-start">
        <SizingPreviewPanel preview={preview} error={previewError} />
      </aside>
    </form>
  );
}

function Field({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`flex flex-col gap-1.5 ${className ?? ""}`}>
      <Label>{label}</Label>
      {children}
    </div>
  );
}

function SizingPreviewPanel({
  preview,
  error,
}: {
  preview: TicketPreviewOut | null;
  error: string | null;
}) {
  if (error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Sizing preview</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-destructive text-sm">{error}</div>
        </CardContent>
      </Card>
    );
  }

  if (!preview) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Sizing preview</CardTitle>
          <CardDescription>Fill in trigger and stop to compute.</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const { sizing, streak, buying_power } = preview;

  const positionValue = parseFloat(sizing.position_value);
  const cash = parseFloat(buying_power.cash);
  const cashEquiv = parseFloat(buying_power.cash_equivalents);
  const cashShortfall = positionValue > cash ? positionValue - cash : 0;
  const canFreeFromCashEquiv = cashShortfall > 0 && cashEquiv >= cashShortfall;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">Sizing preview</CardTitle>
          {sizing.capped && <Badge variant="destructive">capped</Badge>}
        </div>
        <CardDescription>
          Risk {fmtPct(sizing.risk_pct)} ({fmtPct(sizing.base_risk_pct)} × {sizing.multiplier})
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <KV label="Shares" value={sizing.shares.toLocaleString()} highlight />
        <KV
          label="Position value"
          value={fmtMoney(sizing.position_value, sizing.equity_currency)}
        />
        <KV
          label="Risk amount"
          value={fmtMoney(sizing.risk_amount, sizing.equity_currency)}
        />
        <KV
          label="Per-share risk"
          value={fmtMoney(sizing.per_share_risk, sizing.equity_currency)}
        />
        <KV
          label={`${sizing.equity_currency} equity basis`}
          value={fmtMoney(sizing.equity_basis, sizing.equity_currency)}
        />

        <div className="border-t pt-3 text-xs">
          <div className="text-muted-foreground mb-1">
            {buying_power.currency} buying power
          </div>
          <div className="flex justify-between">
            <span>Cash</span>
            <span className="tabular-nums">
              {fmtMoney(buying_power.cash, buying_power.currency)}
            </span>
          </div>
          {cashEquiv > 0 && (
            <div className="flex justify-between">
              <span>Parked (cash-eq)</span>
              <span className="tabular-nums">
                {fmtMoney(buying_power.cash_equivalents, buying_power.currency)}
              </span>
            </div>
          )}
          {cashShortfall > 0 && (
            <div className="mt-1">
              {canFreeFromCashEquiv ? (
                <span className="text-amber-600 dark:text-amber-400">
                  Cash short by {fmtMoney(cashShortfall, buying_power.currency)} — free
                  it by selling parked cash-equivalents.
                </span>
              ) : (
                <span className="text-destructive">
                  Cash short by {fmtMoney(cashShortfall, buying_power.currency)} — even
                  liquidating cash-equivalents won&rsquo;t cover this trade.
                </span>
              )}
            </div>
          )}
        </div>

        <div className="border-t pt-3 text-xs">
          <div className="text-muted-foreground mb-1">Streak</div>
          <div className="flex justify-between">
            <span>
              {streak.consecutive_wins}W / {streak.consecutive_losses}L
              {streak.last_outcome && ` · last ${streak.last_outcome}`}
            </span>
            <span className="tabular-nums">{streak.multiplier}×</span>
          </div>
          {streak.cooldown_active && (
            <div className="text-destructive mt-1">Cooldown active — risk halved.</div>
          )}
        </div>

        {sizing.warnings.length > 0 && (
          <div className="border-t pt-3 text-xs">
            <div className="text-muted-foreground mb-1">Warnings</div>
            <ul className="space-y-1">
              {sizing.warnings.map((w, i) => (
                <li key={i} className="text-amber-600 dark:text-amber-400">
                  · {w}
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function KV({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className={`tabular-nums ${highlight ? "text-lg font-semibold" : ""}`}>{value}</span>
    </div>
  );
}
