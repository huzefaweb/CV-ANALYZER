"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

// Mirrors ProgressPanel.tsx's poll cadence (Story 4.7 AC#2's "no faster
// than 2 seconds" floor) — no reason for this control to poll more
// aggressively than the one other live-status surface in this app already
// does.
const POLL_INTERVAL_MS = 2500;

type QuestionSetState = "NotGenerated" | "Generating" | "Recovering" | "Retrying" | "Complete" | "Failed";

// Review fix (Blind Hunter, Medium): AC#2 requires "attempt context" in the
// Recovering/Retrying copy, mirroring progress_projection.py's own
// "Retrying - Attempt 2 of 2" precedent. "Attempt 2 of 2" is a correct
// literal, not a guess: question_set_projection.py's RETRYING branch is
// only ever reached when failure_reason is set on a 'queued' row, which
// (per that module's own documented reasoning, matching
// progress_projection.py's identical rule) only ever happens after attempt
// has just been bumped to 2 of the fixed MAX_ATTEMPTS=2 budget.
const IN_FLIGHT_COPY: Record<"Generating" | "Recovering" | "Retrying", string> = {
  Generating: "Generating interview questions…",
  Recovering: "Recovering interview question generation…",
  Retrying: "Retrying interview question generation… (attempt 2 of 2)",
};

// Story 7.1/7.2: NotGenerated shows the generate control; Generating/
// Recovering/Retrying show static status text only (no control while a
// generation cycle is active — "no edit/customize/regenerate control
// exists" is structural, not merely hidden by a flag); Complete shows
// static confirmation text only (this story does not render question
// content — Story 7.3's print projection is the first consumer of it);
// Failed shows one isolated retry control. A 409 (idempotency_key_conflict
// or active_generation_exists) on generate still means a job exists
// server-side, so it resolves like a success — mirrors
// resolveShortlistOutcome's "prefer the real persisted state over the
// client's guess" lesson, only a genuine network/5xx failure rolls back.
export default function QuestionSetControl({
  candidateId,
  revisionNumber,
  initialState,
}: {
  candidateId: string;
  revisionNumber: number;
  initialState: QuestionSetState;
}) {
  const router = useRouter();
  const [state, setState] = useState<QuestionSetState>(initialState);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // A generation cycle finishes entirely server-side (the gateway's
  // question-set coordinator) — nothing tells this already-mounted
  // component when that happens. router.refresh() re-fetches the Candidate
  // Report's server data (including this control's own `initialState`
  // prop and, once Complete, the question list itself) without a full page
  // reload; this effect re-syncs local state whenever that refreshed prop
  // arrives, since useState(initialState) only reads it once, on mount.
  useEffect(() => {
    setState(initialState);
  }, [initialState]);

  useEffect(() => {
    if (state !== "Generating" && state !== "Recovering" && state !== "Retrying") return;
    const interval = setInterval(() => router.refresh(), POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [state, router]);

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

  async function handleRetry() {
    setPending(true);
    setError(null);

    let response: Response;
    try {
      response = await fetch(
        `/api/workspace/candidates/${encodeURIComponent(candidateId)}/question-set/retry?revision_number=${revisionNumber}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
        },
      );
    } catch {
      setPending(false);
      setError("Unable to reach the server. Try again.");
      return;
    }

    setPending(false);
    let data: { question_set_state?: QuestionSetState } | null = null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }

    if (response.status === 202 && data?.question_set_state) {
      // Prefer the real persisted state the coordinator/job actually
      // carries over an optimistic guess (mirrors handleGenerate's own
      // 409-still-means-in-flight lesson).
      setState(data.question_set_state);
      return;
    }
    setError("Unable to retry question generation. Try again.");
  }

  if (state === "Generating" || state === "Recovering" || state === "Retrying") {
    return <p aria-live="polite">{IN_FLIGHT_COPY[state]}</p>;
  }

  if (state === "Complete") {
    return <p aria-live="polite">Interview questions ready.</p>;
  }

  if (state === "Failed") {
    return (
      <div>
        <button type="button" disabled={pending} aria-disabled={pending} onClick={handleRetry}>
          Retry Interview Questions
        </button>
        {error ? (
          <p role="alert" aria-live="assertive">
            {error}
          </p>
        ) : null}
      </div>
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
