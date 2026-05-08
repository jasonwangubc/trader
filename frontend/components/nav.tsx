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
  Sun,
  Moon,
  Menu,
  X,
} from "lucide-react";
import { API_URL } from "@/lib/api";
import { SymbolSearch } from "@/components/symbol-search";

const NAV = [
  { href: "/",           label: "Dashboard",  Icon: LayoutDashboard },
  { href: "/routine",    label: "Routine",    Icon: ClipboardList },
  { href: "/screener",   label: "Screener",   Icon: Search },
  { href: "/watchlist",  label: "Watchlist",  Icon: Eye },
  { href: "/tickets",    label: "Tickets",    Icon: FileText },
  { href: "/options",    label: "Options",    Icon: LineChart },
  { href: "/positions",  label: "Positions",  Icon: TrendingUp },
  { href: "/accounts",   label: "Accounts",   Icon: Wallet },
  { href: "/journal",    label: "Journal",    Icon: BookOpen },
  { href: "/backtest",   label: "Backtest",   Icon: FlaskConical },
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
    <nav className="flex h-full flex-col">
      {/* Logo */}
      <div className="flex items-center gap-2 px-4 py-4">
        <Shield className="text-primary h-5 w-5" />
        <span className="text-lg font-semibold tracking-tight">trader</span>
      </div>

      {/* Symbol search */}
      <SymbolSearch />

      {/* Links */}
      <div className="flex-1 space-y-0.5 px-2">
        {NAV.map(({ href, label, Icon }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              onClick={() => setOpen(false)}
              className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                active
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground"
              }`}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {label}
              {label === "Tickets" && monitor && monitor.armed_tickets > 0 && (
                <span className="bg-primary text-primary-foreground ml-auto rounded-full px-1.5 py-0.5 text-[10px] font-semibold">
                  {monitor.armed_tickets}
                </span>
              )}
            </Link>
          );
        })}
      </div>

      {/* Bottom section */}
      <div className="border-t px-3 py-4 space-y-3">
        {/* Dark mode toggle */}
        <div className="flex items-center justify-between text-xs">
          <span className="text-muted-foreground">Theme</span>
          <button
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            className="text-muted-foreground hover:text-foreground rounded p-1 hover:bg-muted transition-colors"
            title="Toggle dark/light mode"
          >
            {theme === "dark" ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
          </button>
        </div>

        {/* Regime */}
        {regime && (
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">Market regime</span>
            <span className={`font-semibold uppercase ${regimeColor}`}>
              {regime.regime}
            </span>
          </div>
        )}

        {/* Monitor status */}
        {monitor && (
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">
              {monitor.market_open ? "Monitor active" : "Monitor idle"}
            </span>
            <span className={`h-2 w-2 rounded-full ${
              monitor.kill_switch ? "bg-destructive" :
              monitor.market_open ? "bg-emerald-500 animate-pulse" : "bg-muted-foreground"
            }`} />
          </div>
        )}

        {/* Kill switch */}
        {monitor && (
          <button
            onClick={toggleKillSwitch}
            className={`w-full rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              monitor.kill_switch
                ? "bg-primary text-primary-foreground hover:bg-primary/90"
                : "border border-destructive/40 text-destructive hover:bg-destructive/10"
            }`}
          >
            {monitor.kill_switch ? "Re-arm monitor" : "Kill switch"}
          </button>
        )}
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
