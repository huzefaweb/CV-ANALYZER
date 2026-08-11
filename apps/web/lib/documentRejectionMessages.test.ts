import assert from "node:assert/strict";
import { test } from "node:test";
import { rejectionMessage } from "./documentRejectionMessages.ts";

test("translates every known gateway rejection category to a plain-language message", () => {
  assert.equal(rejectionMessage("size_limit"), "This file is larger than 10 MB.");
  assert.equal(rejectionMessage("password_protected"), "This file is password-protected.");
});

test("falls back to a generic message for an unknown category", () => {
  assert.equal(rejectionMessage("some_future_category"), "This file could not be uploaded.");
});

test("falls back to a generic message for a non-string or missing category", () => {
  assert.equal(rejectionMessage(undefined), "This file could not be uploaded.");
  assert.equal(rejectionMessage(null), "This file could not be uploaded.");
  assert.equal(rejectionMessage(42), "This file could not be uploaded.");
});
