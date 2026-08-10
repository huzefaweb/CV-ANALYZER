import { NextRequest, NextResponse } from "next/server";
import { gatewayFetch } from "@/lib/gateway";

export async function POST(request: NextRequest) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid request body" }, { status: 400 });
  }

  const gatewayResponse = await gatewayFetch("/identity/register", {
    method: "POST",
    body: JSON.stringify(body),
  });
  const data = await gatewayResponse.json();
  return NextResponse.json(data, { status: gatewayResponse.status });
}
