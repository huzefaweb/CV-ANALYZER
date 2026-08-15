import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { gatewayFetch, SESSION_COOKIE } from "@/lib/gateway";
import ProgressPanel from "./ProgressPanel";

export const dynamic = "force-dynamic";

type WorkspaceSession = { id: string; status: string; created_at: string };

// AD-3/UX-DR9: missing, malformed, and cross-owner ids all render the same
// neutral "Authorization denied" copy — no object/owner/Organization/
// lifecycle detail is ever disclosed here.
export default async function WorkspaceSessionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE)?.value;
  if (!token) redirect("/login");

  const response = await gatewayFetch(`/workspace/sessions/${encodeURIComponent(id)}`, {
    headers: { Cookie: `${SESSION_COOKIE}=${token}` },
  });

  // A token existed but the gateway rejected it (expired/revoked) — route
  // through the neutral Session expired surface, not straight to Login.
  if (response.status === 401) {
    redirect(`/session-expired?return_to=${encodeURIComponent(`/workspace/sessions/${id}`)}`);
  }
  // Not yet admitted — unchanged deferred limitation (deferred-work.md).
  if (response.status === 403) redirect("/login");

  // AD-3/AC#3: only the gateway's neutral 404 (missing/malformed/cross-owner
  // — all identical) renders this copy. Any other failure (5xx, network) is
  // a real error, not an authorization denial, and must not be presented as
  // one — surface it distinctly instead of masking it as neutral.
  if (response.status === 404) {
    return (
      <main id="main">
        <h1>Authorization denied</h1>
        <p>This isn&apos;t available.</p>
        <p>
          <a href="/workspace">Return to workspace</a>
        </p>
      </main>
    );
  }
  if (!response.ok) throw new Error(`Session request failed: ${response.status}`);

  const session: WorkspaceSession = await response.json();

  return (
    <main id="main">
      <h1>Analysis Progress</h1>
      <p className="meta">
        Status: {session.status} · Started {new Date(session.created_at).toLocaleString()}
      </p>
      <ProgressPanel sessionId={session.id} />
      <p>
        <a href="/workspace">Return to workspace</a>
      </p>
    </main>
  );
}
