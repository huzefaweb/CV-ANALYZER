import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { gatewayFetch, SESSION_COOKIE } from "@/lib/gateway";

// Story 4.7: same-origin proxy for the client-polled Progress projection.
// The gateway is a separate origin with no CORS configured (a known,
// disclosed gap — see CLAUDE.md), and the session cookie is HttpOnly, so a
// browser fetch cannot reach the gateway directly or attach the cookie
// itself. Every poll re-runs this handler, which forwards the cookie fresh
// each time — there is no caching layer here for authorization to go stale
// behind (NFR-7).
export async function GET(_request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE)?.value;

  let gatewayResponse: Response;
  try {
    gatewayResponse = await gatewayFetch(`/workspace/sessions/${encodeURIComponent(id)}/progress`, {
      headers: token ? { Cookie: `${SESSION_COOKIE}=${token}` } : undefined,
    });
  } catch {
    return NextResponse.json({ detail: "Unable to reach the server" }, { status: 502 });
  }

  let data: unknown;
  try {
    data = await gatewayResponse.json();
  } catch {
    return NextResponse.json({ detail: "The server returned an unexpected response" }, { status: 502 });
  }
  return NextResponse.json(data, { status: gatewayResponse.status });
}
