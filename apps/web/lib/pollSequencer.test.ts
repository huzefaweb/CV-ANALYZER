import assert from "node:assert/strict";
import { test } from "node:test";
import { createPollSequencer } from "./pollSequencer.ts";

test("out-of-order resolution: a later-issued request wins even if it resolves first", () => {
  const sequencer = createPollSequencer();
  const first = sequencer.issue();
  const second = sequencer.issue();

  // Second resolves before first (network reordering) — it is current.
  assert.equal(sequencer.isCurrent(second), true);
  // First resolving afterward is stale — it must never overwrite second's result.
  assert.equal(sequencer.isCurrent(first), false);
});

test("duplicate checks do not consume or mutate state", () => {
  const sequencer = createPollSequencer();
  const id = sequencer.issue();

  assert.equal(sequencer.isCurrent(id), true);
  assert.equal(sequencer.isCurrent(id), true);
  assert.equal(sequencer.isCurrent(id), true);
});

test("issued ids are strictly monotonic", () => {
  const sequencer = createPollSequencer();
  let previous = sequencer.issue();
  for (let i = 0; i < 10; i++) {
    const next = sequencer.issue();
    assert.ok(next > previous);
    previous = next;
  }
});

test("an id that was never issued is never current", () => {
  const sequencer = createPollSequencer();
  sequencer.issue();
  assert.equal(sequencer.isCurrent(9999), false);
});
