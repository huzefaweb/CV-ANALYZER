import { NextRequest, NextResponse } from "next/server";
import { gatewayFetch, SESSION_COOKIE } from "@/lib/gateway";

function parseSessionCookie(setCookieHeaders: string[]): { token: string; maxAgeSeconds: number } | null {
  const raw = setCookieHeaders.find((header) => header.startsWith(`${SESSION_COOKIE}=`));
  if (!raw) return null;

  const token = raw.match(new RegExp(`${SESSION_COOKIE}=([^;]+)`))?.[1];
  const maxAge = raw.match(/Max-Age=(\d+)/i)?.[1];
  if (!token || !maxAge) return null;

  return { token, maxAgeSeconds: Number(maxAge) };
}

export async function POST(request: NextRequest) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid request body" }, { status: 400 });
  }

  const gatewayResponse = await gatewayFetch("/identity/login", {
    method: "POST",
    body: JSON.stringify(body),
  });
  const data = await gatewayResponse.json();

  if (!gatewayResponse.ok) {
    return NextResponse.json(data, { status: gatewayResponse.status });
  }

  // Next.js owns the browser-facing cookie (AD-2/AD-21): the gateway's
  // opaque session token (and its TTL, read from the same Set-Cookie header
  // rather than a duplicated constant) becomes an HttpOnly/Secure/same-origin
  // cookie here — the browser never talks to the gateway directly.
  const parsed = parseSessionCookie(gatewayResponse.headers.getSetCookie());
  if (!parsed) {
    return NextResponse.json({ detail: "Authentication failed" }, { status: 502 });
  }

  const response = NextResponse.json(data, { status: gatewayResponse.status });
  response.cookies.set(SESSION_COOKIE, parsed.token, {
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    path: "/",
    maxAge: parsed.maxAgeSeconds,
  });
  return response;
}
