/**
 * API client — routes all calls through the Next.js proxy (/api/backend/...)
 * which injects the Clerk user ID as a trusted X-User-Id header.
 *
 * DIRECT_API_URL is only used by client-side components that make fetch()
 * calls directly (symbol-search, recommendations-panel, etc.) — those also
 * go through the proxy via relative URLs.
 */

// The proxy strips /api/backend and forwards to the FastAPI backend.
// In server components, relative URLs work because Next.js knows its own origin.
// In client components, NEXT_PUBLIC_API_URL should point to the Next.js server
// (e.g. http://localhost:3000), NOT directly to FastAPI.
export const API_URL = typeof window === "undefined"
  ? (process.env.NEXTAUTH_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:3000") + "/api/backend"
  : "/api/backend";

// Direct FastAPI URL — used only by legacy client-side fetch calls that
// haven't been migrated to go through the proxy yet.
export const DIRECT_API_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8002";

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

type FetchOpts = RequestInit & { noCache?: boolean };

export async function api<T>(path: string, opts: FetchOpts = {}): Promise<T> {
  const { noCache = true, ...init } = opts;
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init.headers ?? {}) },
    cache: noCache ? "no-store" : init.cache,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, body || res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export type Health = { status: string };
export type HealthDb = { status: string; db: number };
