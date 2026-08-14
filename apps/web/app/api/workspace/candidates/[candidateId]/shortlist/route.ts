import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { gatewayFetch, SESSION_COOKIE } from "@/lib/gateway";

// Story 6.4: same-origin proxy for the Shortlist mutation (mirrors
// .../evidence/[requirementId]/review/route.ts's exact shape) — the gateway
// is a separate origin with no CORS configured and the session cookie is
// HttpOnly, so a browser fetch cannot reach the gateway directly.
export async function PUT(request: NextRequest, { params }: { params: Promise<{ candidateId: string }> }) {
  const { candidateId } = await params;

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid request body" }, { status: 400 });
  }

  const record = body as Record<string, unknown> | null;
  const state = record?.state;
  const expectedVersion = record?.expected_version;
  const idempotencyKey = record?.idempotency_key;
  if (
    typeof body !== "object" ||
    body === null ||
    (state !== "Shortlisted" && state !== "NotShortlisted") ||
    typeof expectedVersion !== "number" ||
    !Number.isInteger(expectedVersion) ||
    expectedVersion < 1 ||
    typeof idempotencyKey !== "string" ||
    idempotencyKey.length < 1 ||
    idempotencyKey.length > 128
  ) {
    return NextResponse.json({ detail: "Invalid request body" }, { status: 400 });
  }

  const revisionNumber = request.nextUrl.searchParams.get("revision_number");
  const revisionQuery = revisionNumber ? `?revision_number=${encodeURIComponent(revisionNumber)}` : "";

  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE)?.value;

  let gatewayResponse: Response;
  try {
    gatewayResponse = await gatewayFetch(
      `/workspace/candidates/${encodeURIComponent(candidateId)}/shortlist${revisionQuery}`,
      {
        method: "PUT",
        headers: token ? { Cookie: `${SESSION_COOKIE}=${token}` } : undefined,
        body: JSON.stringify({ state, expected_version: expectedVersion, idempotency_key: idempotencyKey }),
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
