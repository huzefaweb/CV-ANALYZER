"use client";

import { useState } from "react";
import { shortlistLabel } from "@/lib/resultsFormatting";
import { resolveShortlistOutcome, type ShortlistState } from "@/lib/shortlistOutcome";

// Story 6.4 (AC#1-3): the first component shared across two independent
// route trees (Results and Report) — both need byte-for-byte identical
// optimistic-toggle/rollback/CAS behavior over the same {state, version}
// shape, so this is one component rather than two bespoke widgets. No
// revision-currency gating (unlike RetryButton.tsx): Shortlist is
// Candidate-owned, not revision-owned, so toggling from a previous-revision
// view is equally valid.
export default function ShortlistToggle({
  candidateId,
  revisionNumber,
  initialState,
  initialVersion,
}: {
  candidateId: string;
  revisionNumber: number;
  initialState: string;
  initialVersion: number;
}) {
  const [shortlist, setShortlist] = useState<ShortlistState>({
    state: initialState === "Shortlisted" ? "Shortlisted" : "NotShortlisted",
    version: initialVersion,
  });
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleToggle() {
    const priorShortlist = shortlist;
    const nextState = shortlist.state === "Shortlisted" ? "NotShortlisted" : "Shortlisted";

    // Optimistic flip (AC#2): shown immediately, rolled back on failure.
    setShortlist({ state: nextState, version: shortlist.version });
    setPending(true);
    setError(null);

    let response: Response;
    try {
      response = await fetch(
        `/api/workspace/candidates/${encodeURIComponent(candidateId)}/shortlist?revision_number=${revisionNumber}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            state: nextState,
            expected_version: priorShortlist.version,
            idempotency_key: crypto.randomUUID(),
          }),
        },
      );
    } catch {
      setShortlist(priorShortlist);
      setPending(false);
      setError("Unable to reach the server. Try again.");
      return;
    }

    let data: unknown;
    try {
      data = await response.json();
    } catch {
      data = null;
    }

    // Focus retention (AC#2): the button stays mounted throughout, never
    // programmatically re-focused.
    const outcome = resolveShortlistOutcome(response.ok, data, priorShortlist);
    setShortlist(outcome.shortlist);
    setPending(false);
    setError(outcome.error);
  }

  return (
    <div>
      <button
        type="button"
        className="shortlist-control"
        disabled={pending}
        aria-disabled={pending}
        aria-live="polite"
        onClick={handleToggle}
      >
        {shortlistLabel(shortlist.state)}
      </button>
      {error ? (
        <p role="alert" aria-live="assertive">
          {error}{" "}
          <button type="button" onClick={handleToggle}>
            Retry
          </button>
        </p>
      ) : null}
    </div>
  );
}
