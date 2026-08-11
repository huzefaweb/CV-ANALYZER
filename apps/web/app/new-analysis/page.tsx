import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { gatewayFetch, SESSION_COOKIE } from "@/lib/gateway";

export const dynamic = "force-dynamic";

// UX-DR5 IA surface / routing target only — the Job Description and
// Document intake form is Story 3.1's scope, not this story's.
export default async function NewAnalysisPage() {
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE)?.value;
  if (!token) redirect("/login");

  const response = await gatewayFetch("/identity/session", {
    headers: { Cookie: `${SESSION_COOKIE}=${token}` },
  });
  // 401 (no/expired/tampered session) and 403 (not yet admitted) both route
  // to Login for now — no distinct "Access pending" surface exists in this
  // repo yet (out of scope here; deferred-work.md tracks it). A genuine
  // server error is not the same failure and must not be masked as one.
  if (response.status === 401 || response.status === 403) redirect("/login");
  if (!response.ok) throw new Error(`Admission check failed: ${response.status}`);

  return (
    <main id="main">
      <h1>New Analysis</h1>
      <p>Job Description and Document intake will be available here in a later story.</p>
    </main>
  );
}
