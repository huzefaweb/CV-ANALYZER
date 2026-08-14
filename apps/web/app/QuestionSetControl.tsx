"use client";

import { useState } from "react";

type QuestionSetState = "NotGenerated" | "Generating";

// Story 7.1 (AC#1): only two states exist yet — no edit/customize/regenerate
// control (Implementation Contract) means the button to trigger a second
// attempt does not exist once a job is active, not merely hidden by a flag.
// A 409 (idempotency_key_conflict or active_generation_exists) still means a
// job exists server-side, so it resolves to "Generating" like a success,
// mirroring resolveShortlistOutcome's "prefer the real persisted state over
// the client's guess" lesson — only a genuine network/5xx failure rolls back.
export default function QuestionSetControl({
  candidateId,
  revisionNumber,
  initialState,
}: {
  candidateId: string;
  revisionNumber: number;
  initialState: QuestionSetState;
}) {
  const [state, setState] = useState<QuestionSetState>(initialState);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleGenerate() {
    setState("Generating");
    setPending(true);
    setError(null);

    let response: Response;
    try {
      response = await fetch(
        `/api/workspace/candidates/${encodeURIComponent(candidateId)}/question-set?revision_number=${revisionNumber}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
        },
      );
    } catch {
      setState("NotGenerated");
      setPending(false);
      setError("Unable to reach the server. Try again.");
      return;
    }

    setPending(false);
    if (response.status === 202 || response.status === 409) {
      // Either a fresh/replayed job (202) or proof one already exists
      // (409) — both mean generation is underway for this Candidate.
      setState("Generating");
      return;
    }
    setState("NotGenerated");
    setError("Unable to start question generation. Try again.");
  }

  if (state === "Generating") {
    return (
      <p aria-live="polite">Generating interview questions…</p>
    );
  }

  return (
    <div>
      <button type="button" disabled={pending} aria-disabled={pending} onClick={handleGenerate}>
        Generate Interview Questions
      </button>
      {error ? (
        <p role="alert" aria-live="assertive">
          {error}{" "}
          <button type="button" onClick={handleGenerate}>
            Retry
          </button>
        </p>
      ) : null}
    </div>
  );
}
