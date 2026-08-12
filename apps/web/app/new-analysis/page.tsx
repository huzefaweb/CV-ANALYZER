import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { gatewayFetch, SESSION_COOKIE } from "@/lib/gateway";
import NewAnalysisWorkspace from "./NewAnalysisWorkspace";
import { type DocumentProjection } from "./DocumentUpload";

export const dynamic = "force-dynamic";

type DraftProjection = {
  id: string;
  status: string;
  job_description_text: string;
  job_description_version: number;
  validation: { non_whitespace_count: number; is_valid: boolean; minimum_required: number };
  preparation: { id: string; status: string; created_at: string } | null;
};

// Story 3.1: POST /new-analysis both admission-checks and idempotently
// gets-or-creates the creator's draft Analysis Session in one call — the
// same single-call pattern already established by workspace/page.tsx and
// workspace/sessions/[id]/page.tsx (admission-check and data-fetch
// combined, not a separate preflight).
export default async function NewAnalysisPage() {
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE)?.value;
  if (!token) redirect("/login");

  const response = await gatewayFetch("/new-analysis", {
    method: "POST",
    headers: { Cookie: `${SESSION_COOKIE}=${token}` },
  });

  // A token existed but the gateway rejected it (expired/revoked) — route
  // through the neutral Session expired surface, not straight to Login.
  if (response.status === 401) redirect(`/session-expired?return_to=${encodeURIComponent("/new-analysis")}`);
  // 403 (not yet admitted) routes to Login for now — no distinct "Access
  // pending" surface exists in this repo yet (out of scope here;
  // deferred-work.md tracks it). A genuine server error is not the same
  // failure and must not be masked as one.
  if (response.status === 403) redirect("/login");

  if (!response.ok) throw new Error(`New Analysis draft request failed: ${response.status}`);

  const draft: DraftProjection = await response.json();

  const documentsResponse = await gatewayFetch(`/new-analysis/${draft.id}/documents`, {
    headers: { Cookie: `${SESSION_COOKIE}=${token}` },
  });
  if (!documentsResponse.ok) throw new Error(`Document list request failed: ${documentsResponse.status}`);
  const { documents }: { documents: DocumentProjection[] } = await documentsResponse.json();

  // Story 3.4 (AC#3, UX-DR7): a locked session reconstructs its prepared
  // inputs read-only, with an inline explanation — never a dead-end and
  // never the editable controls (locked inputs do not offer edits).
  if (draft.status !== "draft") {
    return (
      <main id="main">
        <h1>New Analysis</h1>
        <div className="notice">
          {draft.preparation
            ? `Analysis preparation is ${draft.preparation.status}. New Analysis will be available again once this session finishes.`
            : "Analysis preparation is in progress. New Analysis will be available again once this session finishes."}
        </div>
        <section aria-labelledby="job-description-locked">
          <h2 id="job-description-locked">Job Description</h2>
          <p>{draft.job_description_text}</p>
        </section>
        <section aria-labelledby="documents-locked">
          <h2 id="documents-locked">Documents</h2>
          <ul>
            {documents.map((document) => (
              <li key={document.id}>
                {document.document_reference} — {document.original_filename}
              </li>
            ))}
          </ul>
        </section>
      </main>
    );
  }

  return (
    <main id="main">
      <h1>New Analysis</h1>
      <NewAnalysisWorkspace
        sessionId={draft.id}
        initialJobDescriptionText={draft.job_description_text}
        initialJobDescriptionVersion={draft.job_description_version}
        initialValidation={draft.validation}
        initialDocuments={documents}
      />
    </main>
  );
}
