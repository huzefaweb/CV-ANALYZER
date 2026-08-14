import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { gatewayFetch, SESSION_COOKIE } from "@/lib/gateway";
import { gateCodeMessage } from "@/lib/gateCodeMessages";
import {
  type EvidencePoint,
  gapText,
  identityReferenceSuffix,
  parseRevisionParam,
  shortlistLabel,
} from "@/lib/resultsFormatting";

export const dynamic = "force-dynamic";

type RankedReport = {
  analysis_session_id: string;
  candidate_id: string;
  document_reference: string;
  original_filename: string;
  display_name: string;
  outcome: "Ranked";
  revision_number: number;
  revision_created_at: string;
  published_at: string;
  is_current: boolean;
  shortlist_state: string;
  strengths: EvidencePoint[];
  gaps: EvidencePoint[];
  interview_focus: EvidencePoint[];
  notice: { version: number; text: string };
  headline_whole_percent: number;
  precise_score_percent: string | null;
};

type NeedsReviewReport = Omit<RankedReport, "outcome" | "headline_whole_percent" | "precise_score_percent"> & {
  outcome: "NeedsReview";
  gate_codes: string[];
};

type CandidateReport = RankedReport | NeedsReviewReport;

// AD-3/UX-DR9: missing, malformed, Failed, stale, and cross-owner ids all
// render the same neutral "Authorization denied" copy — the identical
// markup workspace/sessions/[id]/results/page.tsx already established.
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

export default async function CandidateReportPage({
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

  const reportPath = revisionNumber
    ? `/workspace/candidates/${encodeURIComponent(candidateId)}/report?revision_number=${revisionNumber}`
    : `/workspace/candidates/${encodeURIComponent(candidateId)}/report`;

  const response = await gatewayFetch(reportPath, { headers: { Cookie: `${SESSION_COOKIE}=${token}` } });

  if (response.status === 401) {
    redirect(`/session-expired?return_to=${encodeURIComponent(`/candidates/${candidateId}/report`)}`);
  }
  if (response.status === 403) redirect("/login");
  if (response.status === 404) return <AuthorizationDenied />;
  if (!response.ok) throw new Error(`Candidate Report request failed: ${response.status}`);

  const data: CandidateReport = await response.json();
  const publishedDate = new Date(data.published_at).toLocaleString();
  const createdDate = new Date(data.revision_created_at).toLocaleString();
  const referenceSuffix = identityReferenceSuffix(data.display_name, data.document_reference);

  return (
    <main id="main">
      <h1>
        {data.display_name}
        {referenceSuffix ? <> — {referenceSuffix}</> : null} · {data.original_filename}
      </h1>

      <p>
        Revision {data.revision_number} · {data.is_current ? "Current published" : "Previous"} · Created{" "}
        {createdDate} · Published {publishedDate}
      </p>

      <div className="notice">
        <p>
          <b>Human verification required.</b> {data.notice.text}
        </p>
      </div>

      <section className="panel">
        {data.outcome === "Ranked" ? (
          <>
            <p>{data.headline_whole_percent}%</p>
            {data.precise_score_percent !== null ? (
              <p>
                Precise calculation value: {data.precise_score_percent}%. Used for deterministic ordering and
                reconciliation. It is not a confidence level, probability, or measure of hiring suitability.
              </p>
            ) : null}
          </>
        ) : (
          <>
            <p>This Document did not reach a score.</p>
            <ul>
              {data.gate_codes.map((code) => (
                <li key={code}>{gateCodeMessage(code)}</li>
              ))}
            </ul>
          </>
        )}
      </section>

      <section className="panel">
        <h2>Findings</h2>
        {data.strengths.length === 0 && data.gaps.length === 0 ? (
          <p>No findings recorded for this Candidate.</p>
        ) : (
          <>
            {data.strengths.length > 0 ? (
              <div>
                <b>Strengths</b>
                <ul>
                  {data.strengths.map((point, i) => (
                    <li key={i}>{point.requirement_text}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {data.gaps.length > 0 ? (
              <div>
                <b>Gaps or uncertainty</b>
                <ul>
                  {data.gaps.map((point, i) => (
                    <li key={i}>{gapText(point)}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </>
        )}
      </section>

      <section className="panel">
        <h2>Interview focus</h2>
        {data.interview_focus.length === 0 ? (
          <p>No validation topics for this Candidate.</p>
        ) : (
          <ul>
            {data.interview_focus.map((point, i) => (
              <li key={i}>{gapText(point)}</li>
            ))}
          </ul>
        )}
      </section>

      <p>{shortlistLabel(data.shortlist_state)}</p>
      <p>No Disputed conclusions in this revision.</p>

      <p>
        <a href={`/workspace/sessions/${data.analysis_session_id}/results?revision=${data.revision_number}`}>
          Back to Results
        </a>
      </p>
    </main>
  );
}
