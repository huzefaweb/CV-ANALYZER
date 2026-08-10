import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { gatewayFetch, SESSION_COOKIE } from "@/lib/gateway";

export async function POST() {
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE)?.value;

  await gatewayFetch("/identity/logout", {
    method: "POST",
    headers: token ? { Cookie: `${SESSION_COOKIE}=${token}` } : undefined,
  });

  const response = NextResponse.json({ status: "logged_out" });
  response.cookies.delete(SESSION_COOKIE);
  return response;
}
