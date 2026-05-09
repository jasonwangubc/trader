"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, useEffect, useCallback } from "react";
import { useTheme } from "next-themes";
import {
  LayoutDashboard,
  FileText,
  TrendingUp,
  Wallet,
  Search,
  BookOpen,
  ClipboardList,
  Shield,
  LineChart,
  Eye,
  FlaskConical,
  Settings,
  Sun,
  Moon,
  Menu,
  X,
} from "lucide-react";
import { UserButton } from "@clerk/nextjs";
import { API_URL } from "@/lib/api";
import { SymbolSearch } from "@/components/symbol-search";

const NAV_SECTIONS = [
  {
    label: "Overview",
    items: [
      { href: "/",        label: "Dashboard", Icon: LayoutDashboard },
      { href: "/routine", label: "Routine",   Icon: ClipboardList },
    ],
  },
  {
    label: "Research",
    items: [
      { href: "/screener",  label: "Screener",  Icon: Search },
      { href: "/watchlist", label: "Watchlist", Icon: Eye },
    ],
  },
  {
    label: "Trade",
    items: [
      { href: "/tickets",   label: "Tickets",   Icon: FileText },
      { href: "/options",   label: "Options",   Icon: LineChart },
      { href: "/positions", label: "Positions", Icon: TrendingUp },
    ],
  },
  {
    label: "Analyze",
    items: [
      { href: "/journal",  label: "Journal",  Icon: BookOpen },
      { href: "/backtest", label: "Backtest", Icon: FlaskConical },
    ],
  },
  {
    label: "Account",
    items: [
      { href: "/accounts", label: "Accounts", Icon: Wallet },
      { href: "/settings", label: "Settings", Icon: Settings },
    ],
  },
];

type MonitorStatus = { running: boolean; armed_tickets: number; kill_switch: boolean; market_open: boolean };
type RegimeStatus  = { regime: string; spy_price: number | null; spy_ma200: number | null };

export function Sidebar() {
  const pathname = usePathname();
  const { theme, setTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const [monitor, setMonitor] = useState<MonitorStatus | null>(null);
  const [regime, setRegime] = useState<RegimeStatus | null>(null);

  useEffect(() => {
    const load = () => {
      fetch(`${API_URL}/api/monitor/status`, { cache: "no-store" }).then(r => r.json()).then(setMonitor).catch(() => {});
      fetch(`${API_URL}/api/regime`, { cache: "no-store" }).then(r => r.json()).then(setRegime).catch(() => {});
    };
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, []);

  const toggleKillSwitch = async () => {
    if (!monitor) return;
    const ep = monitor.kill_switch ? "disable" : "enable";
    await fetch(`${API_URL}/api/monitor/kill-switch/${ep}`, { method: "POST" });
    fetch(`${API_URL}/api/monitor/status`).then(r => r.json()).then(setMonitor).catch(() => {});
  };

  const regimeColor =
    regime?.regime === "bull"    ? "text-emerald-600 dark:text-emerald-400" :
    regime?.regime === "caution" ? "text-amber-600 dark:text-amber-400" :
    regime?.regime === "bear"    ? "text-destructive" : "text-muted-foreground";

  const content = (
    <nav className="flex h-full flex-col overflow-hidden">
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-4 pt-5 pb-3">
        <div className="bg-primary/15 flex h-7 w-7 items-center justify-center rounded-lg">
          <Shield className="text-primary h-4 w-4" />
        </div>
        <span className="text-base font-semibold tracking-tight">trader</span>
        <button
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          className="text-muted-foreground hover:text-foreground ml-auto rounded-md p-1 hover:bg-muted/60 transition-colors"
          title="Toggle theme"
        >
          {theme === "dark" ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
        </button>
      </div>

      {/* Symbol search */}
      <SymbolSearch />

      {/* Sectioned navigation */}
      <div className="flex-1 overflow-y-auto px-2 py-1 space-y-4">
        {NAV_SECTIONS.map(({ label, items }) => (
          <div key={label}>
            <p className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground/60">
              {label}
            </p>
            <div className="space-y-0.5">
              {items.map(({ href, label: itemLabel, Icon }) => {
                const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
                return (
                  <Link
                    key={href}
                    href={href}
                    onClick={() => setOpen(false)}
                    className={`group flex items-center gap-2.5 rounded-lg px-3 py-1.5 text-sm transition-all ${
                      active
                        ? "bg-primary/12 text-primary font-medium"
                        : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
                    }`}
                  >
                    <Icon className={`h-3.5 w-3.5 shrink-0 ${active ? "text-primary" : "text-muted-foreground group-hover:text-foreground"}`} />
                    {itemLabel}
                    {itemLabel === "Tickets" && monitor && monitor.armed_tickets > 0 && (
                      <span className="bg-primary text-primary-foreground ml-auto rounded-full px-1.5 py-0.5 text-[10px] font-bold tabular-nums">
                        {monitor.armed_tickets}
                      </span>
                    )}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {/* Status footer */}
      <div className="border-t border-border/50 px-3 py-3 space-y-2">
        {/* Regime pill */}
        {regime && (
          <div className={`flex items-center justify-between rounded-lg px-2.5 py-1.5 text-xs ${
            regime.regime === "bull"    ? "bg-emerald-500/10" :
            regime.regime === "caution" ? "bg-amber-500/10"   :
            "bg-destructive/10"
          }`}>
            <span className="text-muted-foreground">Regime</span>
            <span className={`font-semibold uppercase tracking-wide ${regimeColor}`}>
              {regime.regime}
            </span>
          </div>
        )}

        {/* Monitor row */}
        {monitor && (
          <div className="flex items-center justify-between px-0.5">
            <button
              onClick={toggleKillSwitch}
              className={`text-xs px-2 py-1 rounded-md font-medium transition-colors ${
                monitor.kill_switch
                  ? "bg-primary/15 text-primary hover:bg-primary/25"
                  : "text-muted-foreground hover:text-destructive hover:bg-destructive/10"
              }`}
            >
              {monitor.kill_switch ? "Re-arm" : "Kill switch"}
            </button>
            <div className="flex items-center gap-1.5">
              <span className={`h-1.5 w-1.5 rounded-full ${
                monitor.kill_switch ? "bg-destructive" :
                monitor.market_open ? "bg-emerald-500 animate-pulse" :
                "bg-muted-foreground/40"
              }`} />
              <span className="text-[10px] text-muted-foreground">
                {monitor.kill_switch ? "halted" : monitor.market_open ? "watching" : "closed"}
              </span>
            </div>
          </div>
        )}

        {/* User */}
        <div className="flex items-center gap-2 pt-0.5">
          <UserButton />
          <span className="text-xs text-muted-foreground truncate">Account</span>
        </div>
      </div>
    </nav>
  );


  return (
    <>
      {/* Mobile toggle */}
      <button
        onClick={() => setOpen(true)}
        className="fixed top-4 left-4 z-40 rounded-md border bg-background p-2 shadow-sm lg:hidden"
      >
        <Menu className="h-4 w-4" />
      </button>

      {/* Mobile overlay */}
      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/40 lg:hidden"
          onClick={() => setOpen(false)}
        />
      )}

      {/* Mobile drawer */}
      <aside className={`fixed inset-y-0 left-0 z-50 w-64 border-r bg-background transition-transform lg:hidden ${open ? "translate-x-0" : "-translate-x-full"}`}>
        <button onClick={() => setOpen(false)} className="absolute top-4 right-4 text-muted-foreground hover:text-foreground">
          <X className="h-4 w-4" />
        </button>
        {content}
      </aside>

      {/* Desktop sidebar */}
      <aside className="hidden lg:fixed lg:inset-y-0 lg:left-0 lg:flex lg:w-56 lg:flex-col lg:border-r lg:bg-background">
        {content}
      </aside>
    </>
  );
}
