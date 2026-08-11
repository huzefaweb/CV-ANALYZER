import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { gatewayFetch, SESSION_COOKIE } from "@/lib/gateway";

export async function PUT(request: NextRequest) {
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
    !("jobDescriptionText" in body) ||
    !("expectedVersion" in body) ||
    typeof (body as Record<string, unknown>).sessionId !== "string" ||
    typeof (body as Record<string, unknown>).jobDescriptionText !== "string" ||
    typeof (body as Record<string, unknown>).expectedVersion !== "number"
  ) {
    return NextResponse.json({ detail: "Invalid request body" }, { status: 400 });
  }
  const { sessionId, jobDescriptionText, expectedVersion } = body as {
    sessionId: string;
    jobDescriptionText: string;
    expectedVersion: number;
  };

  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE)?.value;

  let gatewayResponse: Response;
  try {
    gatewayResponse = await gatewayFetch(`/new-analysis/${encodeURIComponent(sessionId)}`, {
      method: "PUT",
      headers: token ? { Cookie: `${SESSION_COOKIE}=${token}` } : undefined,
      body: JSON.stringify({
        job_description_text: jobDescriptionText,
        expected_version: expectedVersion,
      }),
    });
  } catch {
    return NextResponse.json({ detail: "Unable to reach the server" }, { status: 502 });
  }

  const data = await gatewayResponse.json();
  return NextResponse.json(data, { status: gatewayResponse.status });
}
