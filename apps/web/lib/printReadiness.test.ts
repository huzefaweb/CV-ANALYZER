import assert from "node:assert/strict";
import { test } from "node:test";
import { derivePrintReadinessState } from "./printReadiness.ts";

test("fonts resolving in time is ready", () => {
  assert.equal(derivePrintReadinessState("resolved"), "ready");
});

test("a readiness timeout is a neutral failure", () => {
  assert.equal(derivePrintReadinessState("timeout"), "failed");
});

test("a rejected readiness check is a neutral failure", () => {
  assert.equal(derivePrintReadinessState("rejected"), "failed");
});
