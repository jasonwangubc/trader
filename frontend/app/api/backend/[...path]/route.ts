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
  const { userId } = await auth();

  // In development without Clerk configured, fall back to the default user
  const effectiveUserId = userId ?? USER_DEFAULT;

  const path = params.path.join("/");
  const targetUrl = `${BACKEND}/api/${path}${req.nextUrl.search}`;

  const headers = new Headers(req.headers);
  headers.set("X-User-Id", effectiveUserId);
  headers.delete("host");   // don't forward the browser's host header

  const body = req.method !== "GET" && req.method !== "HEAD"
    ? await req.arrayBuffer()
    : undefined;

  const response = await fetch(targetUrl, {
    method: req.method,
    headers,
    body,
    // Don't follow redirects — proxy them as-is
    redirect: "manual",
  });

  // Stream the response back, preserving status, headers, body
  const responseHeaders = new Headers(response.headers);
  // Allow the browser to read the response
  responseHeaders.delete("content-encoding"); // Next.js handles compression

  return new NextResponse(response.body, {
    status: response.status,
    headers: responseHeaders,
  });
}

export const GET     = (req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) => ctx.params.then(p => proxy(req, p));
export const POST    = (req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) => ctx.params.then(p => proxy(req, p));
export const PUT     = (req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) => ctx.params.then(p => proxy(req, p));
export const PATCH   = (req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) => ctx.params.then(p => proxy(req, p));
export const DELETE  = (req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) => ctx.params.then(p => proxy(req, p));
