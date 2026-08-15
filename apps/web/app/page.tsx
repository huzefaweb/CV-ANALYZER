import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { SESSION_COOKIE } from "@/lib/gateway";

// Reads the request-scoped session cookie — must not be statically
// prerendered (mirrors workspace/page.tsx's reasoning).
export const dynamic = "force-dynamic";

// "/" is not a product surface — it only routes to the signed-in workspace
// or to Login. workspace/page.tsx independently re-checks the cookie with
// the gateway (401/403/expired handling); this redirect is not a trust
// boundary, just traffic direction.
export default async function Page() {
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE)?.value;
  redirect(token ? "/workspace" : "/login");
}
