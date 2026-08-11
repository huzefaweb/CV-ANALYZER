import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/lib/gateway";

const GATEWAY_URL = process.env.GATEWAY_URL ?? "http://localhost:8000";

// Does not use gatewayFetch: that helper unconditionally defaults to
// Content-Type: application/json, which would break multipart/form-data —
// a FormData body needs the runtime to set its own
// "multipart/form-data; boundary=..." header, never a manually-set one.
export async function POST(request: NextRequest) {
  let incoming: FormData;
  try {
    incoming = await request.formData();
  } catch {
    return NextResponse.json({ detail: "Invalid request body" }, { status: 400 });
  }

  const sessionId = incoming.get("sessionId");
  const file = incoming.get("file");
  const idempotencyKey = incoming.get("idempotencyKey");

  if (typeof sessionId !== "string" || !(file instanceof Blob) || typeof idempotencyKey !== "string") {
    return NextResponse.json({ detail: "Invalid request body" }, { status: 400 });
  }

  const outbound = new FormData();
  outbound.set("file", file, (file as File).name ?? "upload");
  outbound.set("idempotency_key", idempotencyKey);

  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE)?.value;

  let gatewayResponse: Response;
  try {
    gatewayResponse = await fetch(`${GATEWAY_URL}/new-analysis/${encodeURIComponent(sessionId)}/documents`, {
      method: "POST",
      headers: token ? { Cookie: `${SESSION_COOKIE}=${token}` } : undefined,
      body: outbound,
    });
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
