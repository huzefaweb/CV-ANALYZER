import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { gatewayFetch, SESSION_COOKIE } from "@/lib/gateway";

export async function POST(request: NextRequest) {
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
    !("expectedJobDescriptionVersion" in body) ||
    !("expectedDocumentVersions" in body) ||
    !("idempotencyKey" in body) ||
    typeof (body as Record<string, unknown>).sessionId !== "string" ||
    typeof (body as Record<string, unknown>).expectedJobDescriptionVersion !== "number" ||
    // typeof null and typeof [] are both "object" — reject both explicitly
    // (review finding: the naive typeof check let either through to the
    // gateway, relying solely on downstream pydantic validation).
    typeof (body as Record<string, unknown>).expectedDocumentVersions !== "object" ||
    (body as Record<string, unknown>).expectedDocumentVersions === null ||
    Array.isArray((body as Record<string, unknown>).expectedDocumentVersions) ||
    typeof (body as Record<string, unknown>).idempotencyKey !== "string"
  ) {
    return NextResponse.json({ detail: "Invalid request body" }, { status: 400 });
  }
  const { sessionId, expectedJobDescriptionVersion, expectedDocumentVersions, idempotencyKey } = body as {
    sessionId: string;
    expectedJobDescriptionVersion: number;
    expectedDocumentVersions: Record<string, number>;
    idempotencyKey: string;
  };

  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE)?.value;

  let gatewayResponse: Response;
  try {
    gatewayResponse = await gatewayFetch(`/new-analysis/${encodeURIComponent(sessionId)}/analyze`, {
      method: "POST",
      headers: token ? { Cookie: `${SESSION_COOKIE}=${token}` } : undefined,
      body: JSON.stringify({
        expected_job_description_version: expectedJobDescriptionVersion,
        expected_document_versions: expectedDocumentVersions,
        idempotency_key: idempotencyKey,
      }),
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
