import assert from "node:assert/strict";
import { test } from "node:test";
import {
  MINIMUM_NON_WHITESPACE_CHARACTERS,
  countNonWhitespaceCharacters,
  isJobDescriptionContentValid,
} from "./validation.ts";

test("counts non-whitespace characters, ignoring spaces/newlines/tabs", () => {
  assert.equal(countNonWhitespaceCharacters("a b\nc\td"), 4);
  assert.equal(countNonWhitespaceCharacters("   \n\t  "), 0);
});

test("is invalid one character below the threshold", () => {
  const text = "a".repeat(MINIMUM_NON_WHITESPACE_CHARACTERS - 1);
  assert.equal(isJobDescriptionContentValid(text), false);
});

test("is valid exactly at the threshold", () => {
  const text = "a".repeat(MINIMUM_NON_WHITESPACE_CHARACTERS);
  assert.equal(isJobDescriptionContentValid(text), true);
});

test("whitespace padding never counts toward the threshold", () => {
  const text = " \n\t".repeat(200);
  assert.equal(isJobDescriptionContentValid(text), false);
});

test("multiline content is counted, not rejected", () => {
  const text = "Role summary.\n\nRequirements:\n" + "x".repeat(180);
  assert.equal(isJobDescriptionContentValid(text), true);
});
