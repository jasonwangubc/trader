export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

type FetchOpts = RequestInit & { /** Disable Next.js fetch caching for live data. */ noCache?: boolean };

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
