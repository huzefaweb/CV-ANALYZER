import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { gatewayFetch, SESSION_COOKIE } from "@/lib/gateway";
import { gateCodeMessage } from "@/lib/gateCodeMessages";
import { componentLabel } from "@/lib/componentLabels";
import {
  type EvidencePoint,
  gapText,
  identityReferenceSuffix,
  parseRevisionParam,
  SHORTLIST_RETENTION_NOTE,
} from "@/lib/resultsFormatting";
import EvidenceSection, { type EvidenceRow } from "./EvidenceSection";
import ShortlistToggle from "@/app/ShortlistToggle";
import QuestionSetControl from "@/app/QuestionSetControl";
import RevisionSelector from "@/app/workspace/sessions/[id]/results/RevisionSelector";

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
  shortlist_version: number;
  strengths: EvidencePoint[];
  gaps: EvidencePoint[];
  interview_focus: EvidencePoint[];
  notice: { version: number; text: string };
  headline_whole_percent: number;
  precise_score_percent: string | null;
  question_set_state: "NotGenerated" | "Generating" | "Recovering" | "Retrying" | "Complete" | "Failed";
};

type NeedsReviewReport = Omit<
  RankedReport,
  "outcome" | "headline_whole_percent" | "precise_score_percent" | "question_set_state"
> & {
  outcome: "NeedsReview";
  gate_codes: string[];
};

type CandidateReport = RankedReport | NeedsReviewReport;

type EvidenceProjection = {
  candidate_id: string;
  revision_number: number;
  is_current: boolean;
  rows: EvidenceRow[];
};

type ReconciliationComponent = {
  component: string;
  base_weight_percent: string;
  applicable: boolean;
  effective_weight_percent: string | null;
  contribution_percent: string | null;
};

type ReconciliationProjection = {
  candidate_id: string;
  revision_number: number;
  is_current: boolean;
  components: ReconciliationComponent[];
  precise_score_percent: string | null;
  headline_whole_percent: number;
};

type Question = {
  number: number;
  category: string;
  text: string;
  source_requirement_id: string;
};

const QUESTION_CATEGORY_LABEL: Record<string, string> = {
  technical_functional: "Technical/functional",
  experience_verification: "Experience verification",
  gap_focused: "Gap-focused",
  behavioral: "Behavioral",
  follow_up: "Follow-up",
};

// Extracted once a second caller needed the identical sentence (review
// finding: it had been hand-duplicated verbatim in the reconciliation
// section below) — reused by both the outcome summary and the
// reconciliation table.
function PreciseValueNotice({ value }: { value: string | null }) {
  if (value === null) return null;
  return (
    <p>
      Precise calculation value: {value}%. Used for deterministic ordering and reconciliation. It is not a
      confidence level, probability, or measure of hiring suitability.
    </p>
  );
}

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

  const encodedId = encodeURIComponent(candidateId);
  const revisionQuery = revisionNumber ? `?revision_number=${revisionNumber}` : "";
  const reportPath = `/workspace/candidates/${encodedId}/report${revisionQuery}`;
  const evidencePath = `/workspace/candidates/${encodedId}/evidence${revisionQuery}`;
  const reconciliationPath = `/workspace/candidates/${encodedId}/reconciliation${revisionQuery}`;

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

  // Evidence exists for both Ranked and Needs Review (Task 2's endpoint
  // qualifies both outcomes), so it is always fetched. Task 3's
  // reconciliation endpoint only serves scoreable (Ranked) Candidates —
  // a Needs Review Candidate's page never calls it (the endpoint's own
  // Needs-Review-404 branch is defensive-only from here).
  // Reuses Story 7.3's print projection as the source of the actual
  // question text — no dedicated "read the Question Set" endpoint exists
  // yet, and this one already carries the exact ten grounded questions
  // once `question_set_state` is Complete, gated by the same authorization
  // this page already passed.
  const questionsPath = `/workspace/candidates/${encodedId}/print${revisionQuery}`;
  const wantsQuestions = data.outcome === "Ranked" && data.question_set_state === "Complete";
  const revisionsPath = `/workspace/sessions/${encodeURIComponent(data.analysis_session_id)}/revisions`;

  const [evidenceResponse, reconciliationResponse, questionsResponse, revisionsResponse] = await Promise.all([
    gatewayFetch(evidencePath, { headers: { Cookie: `${SESSION_COOKIE}=${token}` } }),
    data.outcome === "Ranked"
      ? gatewayFetch(reconciliationPath, { headers: { Cookie: `${SESSION_COOKIE}=${token}` } })
      : Promise.resolve(null),
    wantsQuestions ? gatewayFetch(questionsPath, { headers: { Cookie: `${SESSION_COOKIE}=${token}` } }) : Promise.resolve(null),
    gatewayFetch(revisionsPath, { headers: { Cookie: `${SESSION_COOKIE}=${token}` } }),
  ]);

  const evidence: EvidenceProjection | null = evidenceResponse.ok ? await evidenceResponse.json() : null;
  const reconciliation: ReconciliationProjection | null =
    reconciliationResponse && reconciliationResponse.ok ? await reconciliationResponse.json() : null;
  const questions: Question[] | null =
    questionsResponse && questionsResponse.ok ? (await questionsResponse.json()).questions ?? null : null;
  const revisions = revisionsResponse.ok ? (await revisionsResponse.json()).revisions : [];

  return (
    <main id="main">
      <h1>
        {data.display_name}
        {referenceSuffix ? <> — {referenceSuffix}</> : null} · {data.original_filename}
      </h1>

      <RevisionSelector
        sessionId={data.analysis_session_id}
        revisions={revisions}
        activeRevisionNumber={data.revision_number}
        publishedBasePath={`/candidates/${data.candidate_id}/report`}
      />

      <p className="meta">
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
            <span className="recon headline tabular">{data.headline_whole_percent}%</span>
            <p className="meta">Resume Evidence score · Rounded display</p>
            <PreciseValueNotice value={data.precise_score_percent} />
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
          <div className="findings">
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
          </div>
        )}
      </section>

      <section className="panel">
        <h2>Evidence</h2>
        {evidence === null ? (
          <p>No Evidence rows recorded for this Candidate.</p>
        ) : (
          <EvidenceSection
            key={data.revision_number}
            candidateId={data.candidate_id}
            revisionNumber={data.revision_number}
            rows={evidence.rows}
          />
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

      {reconciliation !== null ? (
        <section className="panel recon">
          <h2>Score reconciliation</h2>
          <div style={{ overflowX: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th>Component</th>
                  <th>Base weight</th>
                  <th>Effective weight</th>
                  <th>Contribution</th>
                </tr>
              </thead>
              <tbody>
                {reconciliation.components.map((c) => (
                  <tr key={c.component}>
                    <td>{componentLabel(c.component)}</td>
                    <td>{c.base_weight_percent}%</td>
                    <td>{c.effective_weight_percent !== null ? `${c.effective_weight_percent}%` : "N/A"}</td>
                    <td>{c.contribution_percent !== null ? `${c.contribution_percent}%` : "N/A"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <PreciseValueNotice value={reconciliation.precise_score_percent} />
          <p>
            Effective weights total 10,000 basis points; largest-remainder handling assigns any integer remainder in
            frozen component order.
          </p>
        </section>
      ) : null}

      <ShortlistToggle
        candidateId={data.candidate_id}
        revisionNumber={data.revision_number}
        initialState={data.shortlist_state}
        initialVersion={data.shortlist_version}
      />
      {data.outcome === "NeedsReview" ? <p>{SHORTLIST_RETENTION_NOTE}</p> : null}

      {data.outcome === "Ranked" ? (
        <section className="question-set">
          <h2>Interview Questions</h2>
          <QuestionSetControl
            candidateId={data.candidate_id}
            revisionNumber={data.revision_number}
            initialState={data.question_set_state}
          />
          {questions !== null ? (
            <ol>
              {questions.map((q) => (
                <li key={q.number}>
                  <span className="category">{QUESTION_CATEGORY_LABEL[q.category] ?? q.category}</span>
                  {q.text}
                  {q.source_requirement_id ? (
                    <span className="qref">Job Requirement: {q.source_requirement_id}</span>
                  ) : null}
                </li>
              ))}
            </ol>
          ) : null}
        </section>
      ) : null}

      <p>
        <a href={`/candidates/${data.candidate_id}/print-prepare?revision=${data.revision_number}`}>
          Prepare to print
        </a>
      </p>

      <p>
        <a href={`/workspace/sessions/${data.analysis_session_id}/results?revision=${data.revision_number}`}>
          Back to Results
        </a>
      </p>
    </main>
  );
}
