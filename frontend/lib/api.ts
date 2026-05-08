/**
 * API client — two modes depending on call context:
 *
 * SERVER-SIDE (Next.js server components / server actions):
 *   Calls FastAPI directly at BACKEND_URL with X-User-Id from auth().
 *   This avoids the self-referential loop problem where
 *   localhost:3000 → /api/backend → localhost:3000 loses the Clerk session.
 *
 * CLIENT-SIDE (browser / client components):
 *   Calls /api/backend/* proxy which adds X-User-Id from the Clerk session.
 */

// Direct backend URL — used by server-side code
const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";

// Proxy path — used by browser code
export const API_URL = typeof window === "undefined" ? BACKEND_URL : "/api/backend";

// Legacy export for client components that still use API_URL directly
export const DIRECT_API_URL = BACKEND_URL;

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

type FetchOpts = RequestInit & { noCache?: boolean };

export async function api<T>(path: string, opts: FetchOpts = {}): Promise<T> {
  const { noCache = true, ...init } = opts;

  const extraHeaders: Record<string, string> = {
    "content-type": "application/json",
    ...(init.headers as Record<string, string> ?? {}),
  };

  // Server-side: read the Clerk user ID directly and set it as a trusted header.
  // We can't rely on the proxy here — self-referential Next.js fetches don't
  // carry the Clerk session cookie, so auth() would return null in the proxy route.
  if (typeof window === "undefined") {
    try {
      const { auth } = await import("@clerk/nextjs/server");
      const { userId } = await auth();
      if (userId) extraHeaders["X-User-Id"] = userId;
    } catch {
      // Clerk not configured (local dev without keys) — X-User-Id omitted,
      // backend will fall back to "user_default".
    }
  }

  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: extraHeaders,
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
