import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { gatewayFetch, SESSION_COOKIE } from "@/lib/gateway";

// Story 5.4: same-origin proxy for Story 5.3's already-built retry command
// (mirrors api/new-analysis/analyze/route.ts's exact shape) — the gateway
// is a separate origin with no CORS configured and the session cookie is
// HttpOnly, so a browser fetch cannot reach the gateway directly.
export async function POST(request: NextRequest, { params }: { params: Promise<{ id: string; candidateId: string }> }) {
  const { id, candidateId } = await params;

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid request body" }, { status: 400 });
  }

  // Review finding: typeof alone accepts NaN/Infinity/negative/zero
  // numbers and an empty/unbounded string — validate the actual shape the
  // gateway expects (a positive revision number, a non-empty key bounded
  // to the gateway's own retry_idempotency_key column width).
  const candidateNumber = (body as Record<string, unknown> | null)?.expected_revision_number;
  const candidateKey = (body as Record<string, unknown> | null)?.idempotency_key;
  if (
    typeof body !== "object" ||
    body === null ||
    typeof candidateNumber !== "number" ||
    !Number.isInteger(candidateNumber) ||
    candidateNumber <= 0 ||
    typeof candidateKey !== "string" ||
    candidateKey.length < 1 ||
    candidateKey.length > 128
  ) {
    return NextResponse.json({ detail: "Invalid request body" }, { status: 400 });
  }
  const { expected_revision_number, idempotency_key } = body as {
    expected_revision_number: number;
    idempotency_key: string;
  };

  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE)?.value;

  let gatewayResponse: Response;
  try {
    gatewayResponse = await gatewayFetch(
      `/workspace/sessions/${encodeURIComponent(id)}/candidates/${encodeURIComponent(candidateId)}/retry`,
      {
        method: "POST",
        headers: token ? { Cookie: `${SESSION_COOKIE}=${token}` } : undefined,
        body: JSON.stringify({ expected_revision_number, idempotency_key }),
      },
    );
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
