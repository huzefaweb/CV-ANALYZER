import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { gatewayFetch, SESSION_COOKIE } from "@/lib/gateway";

export async function POST(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid request body" }, { status: 400 });
  }

  if (
    typeof body !== "object" ||
    body === null ||
    !("sessionId" in body) ||
    !("expectedVersion" in body) ||
    !("idempotencyKey" in body) ||
    typeof (body as Record<string, unknown>).sessionId !== "string" ||
    typeof (body as Record<string, unknown>).expectedVersion !== "number" ||
    typeof (body as Record<string, unknown>).idempotencyKey !== "string"
  ) {
    return NextResponse.json({ detail: "Invalid request body" }, { status: 400 });
  }
  const { sessionId, expectedVersion, idempotencyKey } = body as {
    sessionId: string;
    expectedVersion: number;
    idempotencyKey: string;
  };

  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE)?.value;

  let gatewayResponse: Response;
  try {
    gatewayResponse = await gatewayFetch(
      `/new-analysis/${encodeURIComponent(sessionId)}/documents/${encodeURIComponent(id)}/remove`,
      {
        method: "POST",
        headers: token ? { Cookie: `${SESSION_COOKIE}=${token}` } : undefined,
        body: JSON.stringify({ expected_version: expectedVersion, idempotency_key: idempotencyKey }),
      }
    );
  } catch {
    return NextResponse.json({ detail: "Unable to reach the server" }, { status: 502 });
  }

  let data: unknown;
  try {
    data = await gatewayResponse.json();
  } catch {
    return NextResponse.json({ detail: "Unexpected response from the server" }, { status: 502 });
  }
  return NextResponse.json(data, { status: gatewayResponse.status });
}
