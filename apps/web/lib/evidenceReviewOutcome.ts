export type ReviewState = { state: "Disputed" | "NoReviewFlag"; version: number };

// Story 6.3 code review fix (Acceptance Auditor, AC#3): resolves the
// rollback target for a Disputed-toggle response. A 409 conflict's body
// carries the gateway's own true persisted state (FastAPI's HTTPException
// nests it under "detail" — the same envelope document_upload.py's
// remove_document/replace_document already use for their own 409s) — that
// real state must win over the client's pre-optimistic guess whenever it's
// available, so the UI never shows a stale/wrong Disputed flag after a
// conflict. Only when the response body carries no usable {disputed,
// version} shape (network failure, malformed response) does this fall back
// to the caller's prior state.
export function resolveReviewOutcome(
  ok: boolean,
  data: unknown,
  priorReview: ReviewState,
): { review: ReviewState; error: string | null } {
  const record = data as Record<string, unknown> | null;

  const fromShape = (shape: Record<string, unknown> | undefined): ReviewState | null => {
    const disputed = shape?.disputed;
    const version = shape?.version;
    if (typeof disputed === "boolean" && typeof version === "number") {
      return { state: disputed ? "Disputed" : "NoReviewFlag", version };
    }
    return null;
  };

  if (ok) {
    const resolved = fromShape(record ?? undefined);
    if (resolved) return { review: resolved, error: null };
    return { review: priorReview, error: "The server returned an unexpected response. Try again." };
  }

  // Non-2xx: prefer the real persisted state nested under "detail" (the
  // gateway's 409-conflict shape); fall back to the prior guess only when
  // the body carries nothing usable (network error, 404, 500).
  const resolved = fromShape(record?.detail as Record<string, unknown> | undefined);
  return { review: resolved ?? priorReview, error: "Could not save your review flag. Try again." };
}
