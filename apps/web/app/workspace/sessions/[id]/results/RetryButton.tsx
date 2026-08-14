"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

// Story 5.4 (AD-12, AC#2): wires Story 5.3's already-built retry command
// into the Failed row. Mirrors AnalyzeButton.tsx's exact disable-on-click/
// idempotency-key/neutral-error-message shape.
export default function RetryButton({
  sessionId,
  candidateId,
  currentRevisionNumber,
}: {
  sessionId: string;
  candidateId: string;
  currentRevisionNumber: number;
}) {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function handleClick() {
    setSubmitting(true);
    setMessage(null);

    let response: Response;
    try {
      response = await fetch(`/api/workspace/sessions/${encodeURIComponent(sessionId)}/candidates/${encodeURIComponent(candidateId)}/retry`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expected_revision_number: currentRevisionNumber,
          idempotency_key: crypto.randomUUID(),
        }),
      });
    } catch {
      setSubmitting(false);
      setMessage("Unable to reach the server. Check your connection and try again.");
      return;
    }

    let data: { retry_created?: boolean } | null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }

    if (response.ok && data?.retry_created) {
      // AC#2: an explicit Recruiter-initiated command response, not the
      // "automatic navigation" AC#3 prohibits for poll/reconnect events.
      router.push(`/workspace/sessions/${sessionId}`);
      return;
    }

    // Story 5.3's uniform neutral envelope deliberately does not
    // distinguish *why* retry_created is false — never show a distinct
    // message per rejection reason (would reopen the ownership/eligibility
    // disclosure channel Story 5.3 explicitly closed).
    setSubmitting(false);
    setMessage("Retry is not available for this Document right now.");
  }

  return (
    <div>
      <button type="button" disabled={submitting} aria-disabled={submitting} onClick={handleClick}>
        {submitting ? "Retrying…" : "Retry in new revision"}
      </button>
      {message ? (
        <p role="alert" aria-live="assertive">
          {message}
        </p>
      ) : null}
    </div>
  );
}
