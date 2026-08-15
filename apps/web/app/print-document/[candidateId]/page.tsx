import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { gatewayFetch, SESSION_COOKIE } from "@/lib/gateway";
import { gateCodeMessage } from "@/lib/gateCodeMessages";
import { componentLabel } from "@/lib/componentLabels";
import { parseRevisionParam } from "@/lib/resultsFormatting";

// Story 7.3 builds this route only as an authorized, already-fetching
// scaffold for Story 7.4 to finish — 7.4 owns print CSS, multi-page
// pagination/reading order, readiness-gated `window.print()` invocation,
// repeated headers/continuation context, monochrome distinctions, and the
// `Private recruiter decision support` footer (AD-18/EXPERIENCE.md's Print
// Contract). Nothing below attempts print-quality layout; it exists so 7.4
// has a real, already-authorized consumption point instead of starting from
// nothing.
export const dynamic = "force-dynamic";

type EvidenceRow = {
  job_requirement_id: string;
  requirement_display_id: string;
  requirement_text: string;
  state: string;
  locator_description: string | null;
  excerpt: string;
  review: { state: string; version: number };
};

type ReconciliationComponent = {
  component: string;
  base_weight_percent: string;
  applicable: boolean;
  effective_weight_percent: string | null;
  contribution_percent: string | null;
};

type Question = {
  number: number;
  category: string;
  text: string;
  source_requirement_id: string;
};

type PrintProjection = {
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
  evidence?: EvidenceRow[];
  reconciliation?: {
    components: ReconciliationComponent[];
    precise_score_percent: string | null;
    headline_whole_percent: number;
  };
  questions?: Question[];
  gate_codes?: string[];
};

// Same neutral copy as candidates/[candidateId]/report/page.tsx and
// candidates/[candidateId]/print-prepare/page.tsx's own copies (AD-3/UX-DR9).
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

export default async function PrintDocumentPage({
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
  const printPath = `/workspace/candidates/${encodedId}/print${revisionQuery}`;

  const response = await gatewayFetch(printPath, { headers: { Cookie: `${SESSION_COOKIE}=${token}` } });

  if (response.status === 401) {
    redirect(`/session-expired?return_to=${encodeURIComponent(`/print-document/${candidateId}`)}`);
  }
  if (response.status === 403) redirect("/login");
  if (response.status === 404) return <AuthorizationDenied />;
  if (!response.ok) throw new Error(`Print projection request failed: ${response.status}`);

  const data: PrintProjection = await response.json();
  // Code review fix (Acceptance Auditor/Blind Hunter, convergent): built
  // from the server's own resolved `data.revision_number`, mirroring
  // print-prepare/page.tsx's identical fix — the raw `revisionQuery` above
  // uses the gateway's `revision_number` key, not this app's `revision` key.
  const reportPath = `/candidates/${encodedId}/report?revision=${data.revision_number}`;

  if (data.blocked) {
    return (
      <main id="main">
        <h1>Printing unavailable</h1>
        <div className="notice">
          <p>
            <b>Human verification required.</b> {data.notice.text}
          </p>
        </div>
        <p>
          This Candidate cannot be printed with Interview Questions yet — the current Question Set is not
          complete.
        </p>
        <p>
          <a href={reportPath}>Return to Report</a>
        </p>
      </main>
    );
  }

  return (
    <main id="main">
      <h1>
        {data.display_name} · {data.document_reference} · {data.original_filename}
      </h1>
      <p>
        Analysis Revision {data.revision_number} · Published {new Date(data.published_at).toLocaleString()}
      </p>
      <p>Trigger: {data.trigger}</p>

      <div className="notice">
        <p>
          <b>Human verification required.</b> {data.notice.text}
        </p>
      </div>

      {data.scope === "ScoredCombined" && data.reconciliation ? (
        <section>
          <h2>Score reconciliation</h2>
          <p>{data.reconciliation.headline_whole_percent}%</p>
          {data.reconciliation.precise_score_percent !== null ? (
            <p>Precise calculation value: {data.reconciliation.precise_score_percent}%</p>
          ) : null}
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
              {data.reconciliation.components.map((c) => (
                <tr key={c.component}>
                  <td>{componentLabel(c.component)}</td>
                  <td>{c.base_weight_percent}%</td>
                  <td>{c.effective_weight_percent !== null ? `${c.effective_weight_percent}%` : "N/A"}</td>
                  <td>{c.contribution_percent !== null ? `${c.contribution_percent}%` : "N/A"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {data.scope === "ReportOnly" && data.gate_codes && data.gate_codes.length > 0 ? (
        <section>
          <h2>This Document did not reach a score</h2>
          <ul>
            {data.gate_codes.map((code) => (
              <li key={code}>{gateCodeMessage(code)}</li>
            ))}
          </ul>
        </section>
      ) : null}

      <section>
        <h2>Evidence</h2>
        {data.evidence && data.evidence.length > 0 ? (
          <ul>
            {data.evidence.map((row) => (
              <li key={row.job_requirement_id}>
                {row.requirement_display_id}: {row.requirement_text} — {row.state}
                {row.excerpt ? ` (“${row.excerpt}”${row.locator_description ? `, ${row.locator_description}` : ""})` : ""}
                {row.review.state === "Disputed" ? " [Disputed]" : ""}
              </li>
            ))}
          </ul>
        ) : (
          <p>No Evidence rows recorded for this Candidate.</p>
        )}
      </section>

      {data.scope === "ScoredCombined" && data.questions ? (
        <section>
          <h2>Interview Questions</h2>
          <ol>
            {data.questions.map((q) => (
              <li key={q.number}>
                {q.text}
                {q.source_requirement_id ? ` (${q.source_requirement_id})` : ""}
              </li>
            ))}
          </ol>
        </section>
      ) : null}
    </main>
  );
}
