import assert from "node:assert/strict";
import { test } from "node:test";
import { resolveReviewOutcome } from "./evidenceReviewOutcome.ts";

const prior = { state: "NoReviewFlag" as const, version: 0 };

test("a 200 success commits the server's returned disputed/version", () => {
  const outcome = resolveReviewOutcome(true, { job_requirement_id: "r1", disputed: true, version: 1 }, prior);
  assert.deepEqual(outcome, { review: { state: "Disputed", version: 1 }, error: null });
});

test("a 409 conflict rolls back to the server's real persisted state, not the prior guess", () => {
  // Code review fix (Acceptance Auditor, AC#3): the old behavior always
  // rolled back to `prior` on any non-200 — this is the exact bug that
  // fix closes. The gateway's 409 body nests the true state under
  // "detail" (FastAPI's HTTPException shape).
  const staleGuess = { state: "Disputed" as const, version: 5 };
  const outcome = resolveReviewOutcome(
    false,
    { detail: { job_requirement_id: "r1", disputed: false, version: 2 } },
    staleGuess,
  );
  assert.deepEqual(outcome, { review: { state: "NoReviewFlag", version: 2 }, error: "Could not save your review flag. Try again." });
});

test("a network failure with no usable body falls back to the prior persisted state", () => {
  const outcome = resolveReviewOutcome(false, null, prior);
  assert.deepEqual(outcome, { review: prior, error: "Could not save your review flag. Try again." });
});

test("a malformed 200 body falls back to the prior state with an error, not a crash", () => {
  const outcome = resolveReviewOutcome(true, { unexpected: "shape" }, prior);
  assert.deepEqual(outcome, { review: prior, error: "The server returned an unexpected response. Try again." });
});

test("a 500 with a non-object body falls back to the prior state", () => {
  const outcome = resolveReviewOutcome(false, "internal error", prior);
  assert.deepEqual(outcome, { review: prior, error: "Could not save your review flag. Try again." });
});
