import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { gatewayFetch, SESSION_COOKIE } from "@/lib/gateway";
import JobDescriptionForm from "./JobDescriptionForm";

export const dynamic = "force-dynamic";

type DraftProjection = {
  id: string;
  job_description_text: string;
  job_description_version: number;
  validation: { non_whitespace_count: number; is_valid: boolean; minimum_required: number };
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

  if (response.status === 409) {
    return (
      <main id="main">
        <h1>New Analysis</h1>
        <div className="notice">
          V1 supports one active Analysis Session at a time. Finish or resolve your current session before starting another.
        </div>
        <p>
          <a href="/workspace">Return to workspace</a>
        </p>
      </main>
    );
  }

  if (!response.ok) throw new Error(`New Analysis draft request failed: ${response.status}`);

  const draft: DraftProjection = await response.json();

  return (
    <main id="main">
      <h1>New Analysis</h1>
      <JobDescriptionForm
        sessionId={draft.id}
        initialText={draft.job_description_text}
        initialVersion={draft.job_description_version}
        initialValidation={draft.validation}
      />
      <p>Document upload will be available here in a later story.</p>
    </main>
  );
}
