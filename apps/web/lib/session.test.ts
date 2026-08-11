import assert from "node:assert/strict";
import { test } from "node:test";
import { isAllowedReturnPath } from "./session.ts";

test("accepts allowlisted protected paths", () => {
  assert.equal(isAllowedReturnPath("/workspace"), true);
  assert.equal(isAllowedReturnPath("/workspace/sessions/abc-123"), true);
  assert.equal(isAllowedReturnPath("/new-analysis"), true);
});

test("rejects everything outside the allowlist", () => {
  assert.equal(isAllowedReturnPath("/login"), false);
  assert.equal(isAllowedReturnPath("/workspacefoo"), false); // sibling-prefix bypass
  assert.equal(isAllowedReturnPath("https://evil.example"), false);
  assert.equal(isAllowedReturnPath("//evil.example"), false); // protocol-relative
  assert.equal(isAllowedReturnPath(""), false);
  assert.equal(isAllowedReturnPath(null), false);
  assert.equal(isAllowedReturnPath(undefined), false);
});

test("rejects traversal and query/fragment injection", () => {
  assert.equal(isAllowedReturnPath("/workspace/../../evil"), false);
  assert.equal(isAllowedReturnPath("/workspace\\evil"), false);
  assert.equal(isAllowedReturnPath("/workspace?x=1"), false);
  assert.equal(isAllowedReturnPath("/workspace#frag"), false);
});

test("rejects a duplicated query key parsed as an array, not a string", () => {
  // Next.js parses `?return_to=a&return_to=b` into a string[] — this must
  // not reach `.startsWith` and throw.
  assert.equal(isAllowedReturnPath(["/workspace", "/evil"]), false);
});
