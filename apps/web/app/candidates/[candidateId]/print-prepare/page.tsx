import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { gatewayFetch, SESSION_COOKIE } from "@/lib/gateway";
import { parseRevisionParam } from "@/lib/resultsFormatting";
import DestinationConfirmationGate from "./DestinationConfirmationGate";

export const dynamic = "force-dynamic";

type PrintPrepareProjection = {
  candidate_id: string;
  document_reference: string;
  original_filename: string;
  display_name: string;
  outcome: "Ranked" | "NeedsReview";
  scope: "ScoredCombined" | "ReportOnly";
  revision_number: number;
  revision_created_at: string;
  published_at: string;
  trigger: string;
  blocked: boolean;
  blocked_reason?: string;
  notice: { version: number; text: string };
};

// AD-3/UX-DR9: same neutral copy report/page.tsx and
// workspace/sessions/[id]/results/page.tsx already each carry their own copy
// of — a third instance matches this codebase's established convention for
// this small (8-line), route-tree-local markup rather than introducing a
// shared component for it now.
function AuthorizationDenied() {
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

export default async function PrintPreparePage({
  params,
  searchParams,
}: {
  params: Promise<{ candidateId: string }>;
  searchParams: Promise<{ revision?: string | string[] }>;
}) {
  const { candidateId } = await params;
  const { revision } = await searchParams;
  const revisionNumber = parseRevisionParam(revision);
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE)?.value;
  if (!token) redirect("/login");

  const encodedId = encodeURIComponent(candidateId);
  const revisionQuery = revisionNumber ? `?revision_number=${revisionNumber}` : "";
  const preparePath = `/workspace/candidates/${encodedId}/print/prepare${revisionQuery}`;

  const response = await gatewayFetch(preparePath, { headers: { Cookie: `${SESSION_COOKIE}=${token}` } });

  if (response.status === 401) {
    redirect(`/session-expired?return_to=${encodeURIComponent(`/candidates/${candidateId}/print-prepare`)}`);
  }
  if (response.status === 403) redirect("/login");
  if (response.status === 404) return <AuthorizationDenied />;
  if (!response.ok) throw new Error(`Print preparation request failed: ${response.status}`);

  const data: PrintPrepareProjection = await response.json();
  // Code review fix (Acceptance Auditor/Blind Hunter, convergent): outgoing
  // browser links use the web route's own `?revision=` query key, not the
  // gateway's `?revision_number=` key `revisionQuery` above was built for —
  // reusing `revisionQuery` verbatim silently dropped revision context on
  // "Back to Report"/"Continue to print" for any non-current revision.
  // Built from the server's own resolved `data.revision_number`, not the
  // raw request param, so it's correct even when `revisionNumber` was
  // omitted (defaults to current) — mirrors report/page.tsx's identical
  // hand-built `?revision=${data.revision_number}` links.
  const webRevisionQuery = `?revision=${data.revision_number}`;
  const reportPath = `/candidates/${encodedId}/report${webRevisionQuery}`;
  const printDocumentPath = `/print-document/${encodedId}${webRevisionQuery}`;

  return (
    <main id="main">
      <h1>Prepare to print</h1>
      <p>
        {data.display_name} · {data.document_reference} · {data.original_filename}
      </p>
      <p>
        Analysis Revision {data.revision_number} · Published {new Date(data.published_at).toLocaleString()}
      </p>
      <p>Trigger: {data.trigger}</p>

      <div className="notice">
        <p>
          <b>Human verification required.</b> {data.notice.text}
        </p>
      </div>

      {data.blocked ? (
        <section className="panel">
          <p>
            This Candidate cannot be printed with Interview Questions yet — the current Question Set is not
            complete.
          </p>
          <p>
            <a href={reportPath}>Return to Report to generate or retry Interview Questions</a>
          </p>
        </section>
      ) : (
        <PrintScopeConfirmation scope={data.scope} continuePath={printDocumentPath} />
      )}

      <p>
        <a href={reportPath}>Back to Report</a>
      </p>
    </main>
  );
}

function PrintScopeConfirmation({
  scope,
  continuePath,
}: {
  scope: "ScoredCombined" | "ReportOnly";
  continuePath: string;
}) {
  const scopeStatement =
    scope === "ScoredCombined"
      ? "This print will include your score reconciliation, Evidence, and all ten Interview Questions."
      : "This print will include Evidence and validation context; it does not include a score.";

  return (
    <section className="panel">
      <p>{scopeStatement}</p>
      <p>
        This print will leave the application and may be visible to your printer or PDF destination. Verify the
        intended destination before continuing.
      </p>
      <DestinationConfirmationGate continuePath={continuePath} />
    </section>
  );
}
