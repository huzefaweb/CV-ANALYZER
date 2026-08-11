import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { gatewayFetch, SESSION_COOKIE } from "@/lib/gateway";

// Reads the request-scoped session cookie — must not be statically
// prerendered (mirrors login/page.tsx's reasoning).
export const dynamic = "force-dynamic";

type WorkspaceSession = { id: string; status: string; created_at: string };

// UX-DR6/UX-DR7: Workspace entry shows only the latest creator-owned
// Analysis Session or routes to New Analysis — never a session list.
export default async function WorkspacePage() {
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE)?.value;
  if (!token) redirect("/login");

  const response = await gatewayFetch("/workspace", {
    headers: { Cookie: `${SESSION_COOKIE}=${token}` },
  });
  if (response.status === 401 || response.status === 403) redirect("/login");
  if (!response.ok) throw new Error(`Workspace request failed: ${response.status}`);

  const data: { session: WorkspaceSession | null } = await response.json();
  if (!data.session) redirect("/new-analysis");

  const { session } = data;

  return (
    <main id="main">
      <h1>Workspace</h1>
      <div className="panel">
        <h2>Your latest Analysis Session</h2>
        <p>Status: {session.status}</p>
        <p>Started: {session.created_at}</p>
        <p>
          <a href={`/workspace/sessions/${session.id}`}>Resume your work</a>
        </p>
      </div>
      <p>
        {/* ponytail: any owned session occupies the one-active-session
            boundary today — no terminal (published) state exists until
            Epic 5, so this can't yet distinguish "occupied" from
            "resumable-but-done". Narrow this once publication exists. */}
        <button type="button" disabled aria-describedby="new-analysis-blocked">
          New Analysis
        </button>
      </p>
      <div className="notice" id="new-analysis-blocked">
        V1 supports one active Analysis Session at a time. Finish or resolve your current session before starting another.
      </div>
    </main>
  );
}
