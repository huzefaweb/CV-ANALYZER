import assert from "node:assert/strict";
import { test } from "node:test";
import { resolveShortlistOutcome } from "./shortlistOutcome.ts";

const prior = { state: "NotShortlisted" as const, version: 1 };

test("a 200 success commits the server's returned state/version", () => {
  const outcome = resolveShortlistOutcome(true, { state: "Shortlisted", version: 2 }, prior);
  assert.deepEqual(outcome, { shortlist: { state: "Shortlisted", version: 2 }, error: null });
});

test("a 409 conflict rolls back to the server's real persisted state, not the prior guess", () => {
  const staleGuess = { state: "Shortlisted" as const, version: 5 };
  const outcome = resolveShortlistOutcome(false, { detail: { state: "NotShortlisted", version: 3 } }, staleGuess);
  assert.deepEqual(outcome, {
    shortlist: { state: "NotShortlisted", version: 3 },
    error: "Could not update Shortlist. Try again.",
  });
});

test("a network failure with no usable body falls back to the prior persisted state", () => {
  const outcome = resolveShortlistOutcome(false, null, prior);
  assert.deepEqual(outcome, { shortlist: prior, error: "Could not update Shortlist. Try again." });
});

test("a malformed 200 body falls back to the prior state with an error, not a crash", () => {
  const outcome = resolveShortlistOutcome(true, { unexpected: "shape" }, prior);
  assert.deepEqual(outcome, { shortlist: prior, error: "The server returned an unexpected response. Try again." });
});

test("a 500 with a non-object body falls back to the prior state", () => {
  const outcome = resolveShortlistOutcome(false, "internal error", prior);
  assert.deepEqual(outcome, { shortlist: prior, error: "Could not update Shortlist. Try again." });
});
