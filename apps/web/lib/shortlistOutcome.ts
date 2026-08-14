export type ShortlistState = { state: "Shortlisted" | "NotShortlisted"; version: number };

// Story 6.4: resolves the rollback target for a Shortlist-toggle response,
// mirroring evidenceReviewOutcome.ts's resolveReviewOutcome exactly (a
// lesson imported from Story 6.3's own code review rather than re-shipping
// the same defect). A 409 conflict's body carries the gateway's own true
// persisted state (FastAPI's HTTPException nests it under "detail") — that
// real state must win over the client's pre-optimistic guess whenever it's
// available. Only when the response body carries no usable {state, version}
// shape (network failure, malformed response) does this fall back to the
// caller's prior state.
export function resolveShortlistOutcome(
  ok: boolean,
  data: unknown,
  priorState: ShortlistState,
): { shortlist: ShortlistState; error: string | null } {
  const record = data as Record<string, unknown> | null;

  const fromShape = (shape: Record<string, unknown> | undefined): ShortlistState | null => {
    const state = shape?.state;
    const version = shape?.version;
    if ((state === "Shortlisted" || state === "NotShortlisted") && typeof version === "number") {
      return { state, version };
    }
    return null;
  };

  if (ok) {
    const resolved = fromShape(record ?? undefined);
    if (resolved) return { shortlist: resolved, error: null };
    return { shortlist: priorState, error: "The server returned an unexpected response. Try again." };
  }

  // Non-2xx: prefer the real persisted state nested under "detail" (the
  // gateway's 409-conflict shape); fall back to the prior guess only when
  // the body carries nothing usable (network error, 404, 500).
  const resolved = fromShape(record?.detail as Record<string, unknown> | undefined);
  return { shortlist: resolved ?? priorState, error: "Could not update Shortlist. Try again." };
}
