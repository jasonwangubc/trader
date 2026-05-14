/**
 * Proxy all /api/backend/* requests to the FastAPI backend,
 * injecting the authenticated Clerk user ID as a trusted header.
 *
 * Browser → Next.js (Clerk validates session here) → FastAPI (reads X-User-Id)
 *
 * FastAPI never faces the internet directly — it trusts X-User-Id because
 * requests can only come through this server-side proxy.
 */
import { auth } from "@clerk/nextjs/server";
import { NextRequest, NextResponse } from "next/server";

const BACKEND = process.env.BACKEND_URL ?? "http://localhost:8002";
const USER_DEFAULT = "user_default";

async function proxy(req: NextRequest, params: { path: string[] }) {
  let effectiveUserId = USER_DEFAULT;
  try {
    const { userId } = await auth();
    if (userId) effectiveUserId = userId;
  } catch { /* Clerk not configured */ }

  const path = params.path.join("/");
  const targetUrl = `${BACKEND}/${path}${req.nextUrl.search}`;

  const headers = new Headers(req.headers);
  headers.set("X-User-Id", effectiveUserId);
  headers.delete("host");

  const body = req.method !== "GET" && req.method !== "HEAD"
    ? await req.arrayBuffer()
    : undefined;

  try {
    const response = await fetch(targetUrl, {
      method: req.method,
      headers,
      body,
      redirect: "manual",
    });

    const responseHeaders = new Headers(response.headers);
    responseHeaders.delete("content-encoding");

    return new NextResponse(response.body, {
      status: response.status,
      headers: responseHeaders,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json(
      { detail: `Backend unreachable (${BACKEND}): ${message}` },
      { status: 502 },
    );
  }
}

export const GET     = (req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) => ctx.params.then(p => proxy(req, p));
export const POST    = (req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) => ctx.params.then(p => proxy(req, p));
export const PUT     = (req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) => ctx.params.then(p => proxy(req, p));
export const PATCH   = (req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) => ctx.params.then(p => proxy(req, p));
export const DELETE  = (req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) => ctx.params.then(p => proxy(req, p));
