import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/lib/gateway";

const GATEWAY_URL = process.env.GATEWAY_URL ?? "http://localhost:8000";

// Does not use gatewayFetch — same reason as ../route.ts's POST: a FormData
// body needs the runtime to set its own multipart boundary header, not the
// JSON default gatewayFetch always applies.
export async function PUT(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let incoming: FormData;
  try {
    incoming = await request.formData();
  } catch {
    return NextResponse.json({ detail: "Invalid request body" }, { status: 400 });
  }

  const sessionId = incoming.get("sessionId");
  const file = incoming.get("file");
  const expectedVersion = incoming.get("expectedVersion");
  const idempotencyKey = incoming.get("idempotencyKey");

  if (
    typeof sessionId !== "string" ||
    !(file instanceof Blob) ||
    typeof expectedVersion !== "string" ||
    typeof idempotencyKey !== "string"
  ) {
    return NextResponse.json({ detail: "Invalid request body" }, { status: 400 });
  }

  const outbound = new FormData();
  outbound.set("file", file, (file as File).name ?? "upload");
  outbound.set("expected_version", expectedVersion);
  outbound.set("idempotency_key", idempotencyKey);

  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE)?.value;

  let gatewayResponse: Response;
  try {
    gatewayResponse = await fetch(
      `${GATEWAY_URL}/new-analysis/${encodeURIComponent(sessionId)}/documents/${encodeURIComponent(id)}`,
      {
        method: "PUT",
        headers: token ? { Cookie: `${SESSION_COOKIE}=${token}` } : undefined,
        body: outbound,
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
