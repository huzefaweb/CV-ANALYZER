"use client";

import { useState } from "react";
import { resolveReviewOutcome, type ReviewState } from "@/lib/evidenceReviewOutcome";

export type EvidenceRow = {
  job_requirement_id: string;
  requirement_display_id: string;
  requirement_text: string;
  state: string;
  locator_description: string | null;
  excerpt: string;
  review: ReviewState;
};

type RowUiState = { pending: boolean; error: string | null };

// Story 6.3 (AC#1-3): the first client component this page needs — a
// server-rendered <details> block (Story 6.2) cannot hold the optimistic
// mutation state or the filter's client-side toggle this story's ACs
// require. Filter/disclosure are plain array operations over already-
// fetched rows (no re-fetch, per 6.2's Dev Notes reasoning for native
// disclosure) so focus/scroll never race a second request.
export default function EvidenceSection({
  candidateId,
  revisionNumber,
  rows,
}: {
  candidateId: string;
  revisionNumber: number;
  rows: EvidenceRow[];
}) {
  const [reviews, setReviews] = useState<Record<string, ReviewState>>(
    () => Object.fromEntries(rows.map((row) => [row.job_requirement_id, row.review])),
  );
  const [rowUi, setRowUi] = useState<Record<string, RowUiState>>({});
  const [showDisputedOnly, setShowDisputedOnly] = useState(false);

  const disputedCount = Object.values(reviews).filter((r) => r.state === "Disputed").length;
  const visibleRows = showDisputedOnly ? rows.filter((row) => reviews[row.job_requirement_id]?.state === "Disputed") : rows;

  async function toggleDisputed(requirementId: string) {
    const current = reviews[requirementId];
    const nextDisputed = current.state !== "Disputed";
    const priorReview = current;

    // Optimistic flip (AC#3): shown immediately, rolled back on failure.
    setReviews((prev) => ({ ...prev, [requirementId]: { state: nextDisputed ? "Disputed" : "NoReviewFlag", version: current.version } }));
    setRowUi((prev) => ({ ...prev, [requirementId]: { pending: true, error: null } }));

    let response: Response;
    try {
      response = await fetch(
        `/api/workspace/candidates/${encodeURIComponent(candidateId)}/evidence/${encodeURIComponent(requirementId)}/review?revision_number=${revisionNumber}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            disputed: nextDisputed,
            expected_version: current.version,
            idempotency_key: crypto.randomUUID(),
          }),
        },
      );
    } catch {
      setReviews((prev) => ({ ...prev, [requirementId]: priorReview }));
      setRowUi((prev) => ({ ...prev, [requirementId]: { pending: false, error: "Unable to reach the server. Try again." } }));
      return;
    }

    let data: unknown;
    try {
      data = await response.json();
    } catch {
      data = null;
    }

    // Stale expected_version (409), success, or any other failure: on a
    // conflict, roll back to the server's actual persisted state (nested
    // under the 409 body's "detail") rather than the client's own
    // pre-optimistic guess — a code review fix (AC#3's "rolls back to
    // persisted state" means the real one, not a local assumption).
    // Focus retention: the button stays mounted throughout, never
    // programmatically re-focused.
    const outcome = resolveReviewOutcome(response.ok, data, priorReview);
    setReviews((prev) => ({ ...prev, [requirementId]: outcome.review }));
    setRowUi((prev) => ({ ...prev, [requirementId]: { pending: false, error: outcome.error } }));
  }

  if (rows.length === 0) {
    return <p>No Evidence rows recorded for this Candidate.</p>;
  }

  return (
    <div>
      {disputedCount > 0 ? (
        <p>
          <label>
            <input
              type="checkbox"
              checked={showDisputedOnly}
              onChange={(e) => setShowDisputedOnly(e.target.checked)}
            />{" "}
            Show Disputed only ({disputedCount})
          </label>
        </p>
      ) : null}
      <p role="status" aria-live="polite">
        {showDisputedOnly ? `Showing Disputed only (${disputedCount})` : `Showing all Evidence (${rows.length})`}
      </p>
      {visibleRows.map((row) => {
        const review = reviews[row.job_requirement_id];
        const ui = rowUi[row.job_requirement_id];
        return (
          <details key={row.job_requirement_id} className="evidence-row">
            <summary>
              <span>
                {row.requirement_display_id} · {row.requirement_text}
              </span>
              <span className={statusClass(row.state)}>{row.state}</span>
              {review.state === "Disputed" ? <span className="status disputed">◇ Disputed</span> : null}
            </summary>
            {row.state === "Not Found" ? (
              <p>No supporting Evidence was located. This is not proof the Candidate lacks the qualification.</p>
            ) : (
              <p className="excerpt">
                {row.excerpt || "No excerpt recorded."}
                {row.locator_description ? ` — ${row.locator_description}` : ""}
              </p>
            )}
            <div className="fields">
              <div>Analysis state: {row.state}</div>
              <div>Recruiter review: {review.state === "Disputed" ? "Disputed" : "No review flag"}</div>
            </div>
            <button
              type="button"
              className="review-control"
              disabled={ui?.pending}
              aria-disabled={ui?.pending}
              onClick={() => toggleDisputed(row.job_requirement_id)}
            >
              {review.state === "Disputed" ? "Clear Disputed" : "Mark Disputed"}
            </button>
            {ui?.error ? (
              <p role="alert" aria-live="assertive">
                {ui.error}{" "}
                <button type="button" onClick={() => toggleDisputed(row.job_requirement_id)}>
                  Retry
                </button>
              </p>
            ) : null}
          </details>
        );
      })}
    </div>
  );
}

function statusClass(state: string): string {
  if (state === "Matched") return "status matched";
  if (state === "Partial") return "status partial";
  if (state === "Not Found") return "status not-found";
  if (state === "Needs Validation") return "status needs-validation";
  return "status";
}
