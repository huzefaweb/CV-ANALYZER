import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { gatewayFetch, SESSION_COOKIE } from "@/lib/gateway";
import { gateCodeMessage } from "@/lib/gateCodeMessages";
import {
  type EvidencePoint,
  gapText,
  identityReferenceSuffix,
  parseRevisionParam,
  SHORTLIST_RETENTION_NOTE,
} from "@/lib/resultsFormatting";
import RevisionSelector, { type RevisionOption } from "./RevisionSelector";
import RetryButton from "./RetryButton";
import ShortlistToggle from "@/app/ShortlistToggle";

export const dynamic = "force-dynamic";

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
  shortlist_version: number;
};

type NeedsReviewRow = {
  candidate_id: string;
  document_reference: string;
  original_filename: string;
  display_name: string;
  gate_codes: string[];
  shortlist_state: string;
  shortlist_version: number;
};

type FailedRow = {
  candidate_id: string;
  document_reference: string;
  original_filename: string;
  display_name: string;
  failure_category: string;
  shortlist_state: string;
  shortlist_version: number;
};

type Notice = { version: number; text: string };

type ResultsProjection =
  | {
      published: false;
      revision_number: null;
      published_at: null;
      is_current: false;
      counts: { ranked: 0; needs_review: 0; failed: 0 };
      ranked: [];
      needs_review: [];
      failed: [];
      notice: Notice;
    }
  | {
      published: true;
      revision_number: number;
      published_at: string;
      is_current: boolean;
      counts: { ranked: number; needs_review: number; failed: number };
      ranked: RankedRow[];
      needs_review: NeedsReviewRow[];
      failed: FailedRow[];
      notice: Notice;
    };

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

export default async function ResultsPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ revision?: string | string[] }>;
}) {
  const { id } = await params;
  const { revision } = await searchParams;
  const revisionNumber = parseRevisionParam(revision);
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE)?.value;
  if (!token) redirect("/login");

  const resultsPath = revisionNumber
    ? `/workspace/sessions/${encodeURIComponent(id)}/results?revision_number=${revisionNumber}`
    : `/workspace/sessions/${encodeURIComponent(id)}/results`;

  // Review finding: the revisions fetch is secondary (only feeds the
  // selector) and must not take down the whole page if it network-fails —
  // Promise.allSettled isolates it from the required `/results` fetch,
  // which still propagates its own failure as before.
  const [resultsOutcome, revisionsOutcome] = await Promise.allSettled([
    gatewayFetch(resultsPath, { headers: { Cookie: `${SESSION_COOKIE}=${token}` } }),
    gatewayFetch(`/workspace/sessions/${encodeURIComponent(id)}/revisions`, {
      headers: { Cookie: `${SESSION_COOKIE}=${token}` },
    }),
  ]);

  if (resultsOutcome.status === "rejected") throw resultsOutcome.reason;
  const response = resultsOutcome.value;

  if (response.status === 401) {
    redirect(`/session-expired?return_to=${encodeURIComponent(`/workspace/sessions/${id}/results`)}`);
  }
  if (response.status === 403) redirect("/login");
  if (response.status === 404) return <AuthorizationDenied />;
  if (!response.ok) throw new Error(`Results request failed: ${response.status}`);

  const data: ResultsProjection = await response.json();
  const revisions: RevisionOption[] =
    revisionsOutcome.status === "fulfilled" && revisionsOutcome.value.ok
      ? (await revisionsOutcome.value.json()).revisions
      : [];

  if (!data.published) {
    return (
      <main id="main">
        <h1>Results</h1>
        {revisions.length > 0 ? (
          <RevisionSelector sessionId={id} revisions={revisions} activeRevisionNumber={null} />
        ) : null}
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
      {revisions.length > 0 ? (
        <RevisionSelector sessionId={id} revisions={revisions} activeRevisionNumber={data.revision_number} />
      ) : null}
      {!data.is_current ? (
        <p>
          <a href={`/workspace/sessions/${id}/results`}>Return to current published revision</a>
        </p>
      ) : null}
      <p className="meta">
        Revision {data.revision_number} · Published {publishedDate}
      </p>
      <div className="notice">
        <p>
          <b>Human verification required.</b> {data.notice.text}
        </p>
      </div>

      <div className="summary">
        <div className="metric">
          <span className="value tabular">{data.counts.ranked}</span>
          <span className="label">Ranked</span>
        </div>
        <div className="metric">
          <span className="value tabular">{data.counts.needs_review}</span>
          <span className="label">Needs Review</span>
        </div>
        <div className="metric">
          <span className="value tabular">{data.counts.failed}</span>
          <span className="label">Failed</span>
        </div>
      </div>

      <section className="outcome-section">
        <h2>Ranked Candidates · {data.counts.ranked}</h2>
        <div className="notice">
          Displayed scores are rounded (round-half-up). Ranking uses the precise, unrounded calculation value
          available in each Candidate Report. A tie exists only when precise scores are equal; when a tie occurs,
          secondary presentation order carries no hiring meaning.
        </div>
        {data.ranked.length === 0 ? (
          <p>No Candidates were ranked in this revision.</p>
        ) : (
          <ol style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {data.ranked.map((row) => (
              <li key={row.candidate_id} value={row.rank_position} className="candidate-row">
                <span className="rank tabular">{row.rank_position}</span>
                <span>
                  <p>
                    <b>{row.display_name}</b>
                    {identityReferenceSuffix(row.display_name, row.document_reference) ? (
                      <> — {identityReferenceSuffix(row.display_name, row.document_reference)}</>
                    ) : null} ·{" "}
                    {row.original_filename}
                  </p>
                  <span className="score tabular">{row.headline_whole_percent}%</span>
                  <div className="findings">
                    {row.strengths.length > 0 ? (
                      <div>
                        <b className="compact">Strengths</b>
                        <ul>
                          {row.strengths.map((point, i) => (
                            <li key={i}>{point.requirement_text}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                    {row.gaps.length > 0 ? (
                      <div>
                        <b className="compact">Gap or uncertainty</b>
                        <ul>
                          {row.gaps.map((point, i) => (
                            <li key={i}>{gapText(point)}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                  </div>
                </span>
                <span className="actions">
                  <ShortlistToggle
                    candidateId={row.candidate_id}
                    revisionNumber={data.revision_number}
                    initialState={row.shortlist_state}
                    initialVersion={row.shortlist_version}
                  />
                  <a className="btn secondary" href={`/candidates/${row.candidate_id}/report?revision=${data.revision_number}`}>
                    View Candidate Report
                  </a>
                </span>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section className="outcome-section">
        <h2>Needs Review · {data.counts.needs_review}</h2>
        {data.needs_review.length === 0 ? (
          <p>No Documents need review in this revision.</p>
        ) : (
          <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {data.needs_review.map((row) => (
              <li key={row.candidate_id} className="candidate-row" style={{ gridTemplateColumns: "1fr auto" }}>
                <span>
                  <span className="status needs-validation">
                    <span aria-hidden="true">△</span> Needs Review
                  </span>
                  <p>
                    <b>{row.display_name}</b>
                    {identityReferenceSuffix(row.display_name, row.document_reference) ? (
                      <> — {identityReferenceSuffix(row.display_name, row.document_reference)}</>
                    ) : null} ·{" "}
                    {row.original_filename}
                  </p>
                  <ul>
                    {row.gate_codes.map((code) => (
                      <li key={code}>{gateCodeMessage(code)}</li>
                    ))}
                  </ul>
                  <p className="meta">{SHORTLIST_RETENTION_NOTE}</p>
                </span>
                <span className="actions">
                  <ShortlistToggle
                    candidateId={row.candidate_id}
                    revisionNumber={data.revision_number}
                    initialState={row.shortlist_state}
                    initialVersion={row.shortlist_version}
                  />
                  <a className="btn secondary" href={`/candidates/${row.candidate_id}/report?revision=${data.revision_number}`}>
                    View Candidate Report
                  </a>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="outcome-section">
        <h2>Failed · {data.counts.failed}</h2>
        {data.failed.length === 0 ? (
          <p>No Documents failed in this revision.</p>
        ) : (
          <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {data.failed.map((row) => (
              <li key={row.candidate_id} className="candidate-row" style={{ gridTemplateColumns: "1fr auto" }}>
                <span>
                  <span className="status failed">
                    <span aria-hidden="true">✕</span> Failed
                  </span>
                  <p>
                    <b>{row.display_name}</b>
                    {identityReferenceSuffix(row.display_name, row.document_reference) ? (
                      <> — {identityReferenceSuffix(row.display_name, row.document_reference)}</>
                    ) : null} ·{" "}
                    {row.original_filename}
                  </p>
                  <p>{row.failure_category}</p>
                  <p className="meta">{SHORTLIST_RETENTION_NOTE}</p>
                </span>
                <span className="actions">
                  <ShortlistToggle
                    candidateId={row.candidate_id}
                    revisionNumber={data.revision_number}
                    initialState={row.shortlist_state}
                    initialVersion={row.shortlist_version}
                  />
                  {data.is_current ? (
                    <RetryButton sessionId={id} candidateId={row.candidate_id} currentRevisionNumber={data.revision_number} />
                  ) : null}
                </span>
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
