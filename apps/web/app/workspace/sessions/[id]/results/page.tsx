import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { gatewayFetch, SESSION_COOKIE } from "@/lib/gateway";
import { gateCodeMessage } from "@/lib/gateCodeMessages";

export const dynamic = "force-dynamic";

type EvidencePoint = { requirement_text: string; state: string };

type RankedRow = {
  candidate_id: string;
  document_reference: string;
  original_filename: string;
  display_name: string;
  rank_position: number;
  tie_group: number;
  presentation_ordinal: number;
  headline_whole_percent: number;
  strengths: EvidencePoint[];
  gaps: EvidencePoint[];
  shortlist_state: string;
};

type NeedsReviewRow = {
  candidate_id: string;
  document_reference: string;
  original_filename: string;
  display_name: string;
  gate_codes: string[];
  shortlist_state: string;
};

type FailedRow = {
  candidate_id: string;
  document_reference: string;
  original_filename: string;
  display_name: string;
  failure_category: string;
  shortlist_state: string;
};

type Notice = { version: number; text: string };

type ResultsProjection =
  | { published: false; revision_number: null; published_at: null; counts: { ranked: 0; needs_review: 0; failed: 0 }; ranked: []; needs_review: []; failed: []; notice: Notice }
  | {
      published: true;
      revision_number: number;
      published_at: string;
      counts: { ranked: number; needs_review: number; failed: number };
      ranked: RankedRow[];
      needs_review: NeedsReviewRow[];
      failed: FailedRow[];
      notice: Notice;
    };

function shortlistLabel(state: string): string {
  return state === "Shortlisted" ? "Shortlisted" : "Not shortlisted";
}

function gapText(point: EvidencePoint): string {
  return point.state === "Needs Validation"
    ? `Needs Validation: ${point.requirement_text}`
    : `Evidence not found for ${point.requirement_text}`;
}

// AD-3/UX-DR9: missing, malformed, and cross-owner ids all render the same
// neutral "Authorization denied" copy — the identical markup
// workspace/sessions/[id]/page.tsx already established.
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

export default async function ResultsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE)?.value;
  if (!token) redirect("/login");

  const response = await gatewayFetch(`/workspace/sessions/${encodeURIComponent(id)}/results`, {
    headers: { Cookie: `${SESSION_COOKIE}=${token}` },
  });

  if (response.status === 401) {
    redirect(`/session-expired?return_to=${encodeURIComponent(`/workspace/sessions/${id}/results`)}`);
  }
  if (response.status === 403) redirect("/login");
  if (response.status === 404) return <AuthorizationDenied />;
  if (!response.ok) throw new Error(`Results request failed: ${response.status}`);

  const data: ResultsProjection = await response.json();

  if (!data.published) {
    return (
      <main id="main">
        <h1>Results</h1>
        <div className="notice">
          <p>Results are not yet published for this Analysis.</p>
        </div>
        <p>
          <a href={`/workspace/sessions/${id}`}>Return to Progress</a>
        </p>
      </main>
    );
  }

  const publishedDate = new Date(data.published_at).toLocaleString();

  return (
    <main id="main">
      <h1>Results</h1>
      <p>
        Revision {data.revision_number} · Published {publishedDate}
      </p>
      <div className="notice">
        <p>
          <b>Human verification required.</b> {data.notice.text}
        </p>
      </div>

      <section className="panel">
        <h2>Ranked Candidates · {data.counts.ranked}</h2>
        <p>
          Displayed scores are rounded (round-half-up). Ranking uses the precise, unrounded calculation value
          available in each Candidate Report. A tie exists only when precise scores are equal; when a tie occurs,
          secondary presentation order carries no hiring meaning.
        </p>
        {data.ranked.length === 0 ? (
          <p>No Candidates were ranked in this revision.</p>
        ) : (
          <ol>
            {data.ranked.map((row) => (
              <li key={row.candidate_id} value={row.rank_position}>
                <p>
                  <b>{row.display_name}</b>
                  {row.display_name !== row.document_reference ? <> — {row.document_reference}</> : null} ·{" "}
                  {row.original_filename}
                </p>
                <p>{row.headline_whole_percent}%</p>
                {row.strengths.length > 0 ? (
                  <div>
                    <b>Strengths</b>
                    <ul>
                      {row.strengths.map((point, i) => (
                        <li key={i}>{point.requirement_text}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {row.gaps.length > 0 ? (
                  <div>
                    <b>Gap or uncertainty</b>
                    <ul>
                      {row.gaps.map((point, i) => (
                        <li key={i}>{gapText(point)}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                <p>{shortlistLabel(row.shortlist_state)}</p>
                <p>
                  <a href={`/candidates/${row.candidate_id}/report`}>View Candidate Report</a>
                </p>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section className="panel">
        <h2>Needs Review · {data.counts.needs_review}</h2>
        {data.needs_review.length === 0 ? (
          <p>No Documents need review in this revision.</p>
        ) : (
          <ul>
            {data.needs_review.map((row) => (
              <li key={row.candidate_id}>
                <p>
                  <span aria-hidden="true">△</span> Needs Review
                </p>
                <p>
                  <b>{row.display_name}</b>
                  {row.display_name !== row.document_reference ? <> — {row.document_reference}</> : null} ·{" "}
                  {row.original_filename}
                </p>
                <ul>
                  {row.gate_codes.map((code) => (
                    <li key={code}>{gateCodeMessage(code)}</li>
                  ))}
                </ul>
                <p>{shortlistLabel(row.shortlist_state)}</p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="panel">
        <h2>Failed · {data.counts.failed}</h2>
        {data.failed.length === 0 ? (
          <p>No Documents failed in this revision.</p>
        ) : (
          <ul>
            {data.failed.map((row) => (
              <li key={row.candidate_id}>
                <p>
                  <span aria-hidden="true">✕</span> Failed
                </p>
                <p>
                  <b>{row.display_name}</b>
                  {row.display_name !== row.document_reference ? <> — {row.document_reference}</> : null} ·{" "}
                  {row.original_filename}
                </p>
                <p>{row.failure_category}</p>
                <p>{shortlistLabel(row.shortlist_state)}</p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <p>
        <a href={`/workspace/sessions/${id}`}>Return to Progress</a>
      </p>
    </main>
  );
}
