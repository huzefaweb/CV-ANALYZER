import assert from "node:assert/strict";
import { test } from "node:test";
import { nextFocusTarget } from "./documentFocusTarget.ts";

test("removing a middle row moves focus to the next row", () => {
  assert.equal(nextFocusTarget(["a", "b", "c"], "b"), "c");
});

test("removing the last row moves focus to the previous row", () => {
  assert.equal(nextFocusTarget(["a", "b", "c"], "c"), "b");
});

test("removing the only row falls back to the upload control (null)", () => {
  assert.equal(nextFocusTarget(["a"], "a"), null);
});

test("removing the first row moves focus to the next row", () => {
  assert.equal(nextFocusTarget(["a", "b", "c"], "a"), "b");
});

test("a key not present in the visible list resolves to null", () => {
  assert.equal(nextFocusTarget(["a", "b"], "z"), null);
});
