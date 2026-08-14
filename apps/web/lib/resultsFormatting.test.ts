import assert from "node:assert/strict";
import { test } from "node:test";
import { gapText, identityReferenceSuffix, parseRevisionParam, shortlistLabel } from "./resultsFormatting.ts";

test("shortlistLabel reflects the two Shortlist states", () => {
  assert.equal(shortlistLabel("Shortlisted"), "Shortlisted");
  assert.equal(shortlistLabel("NotShortlisted"), "Not shortlisted");
});

test("gapText phrases Needs Validation and Not Found distinctly", () => {
  assert.equal(
    gapText({ requirement_text: "Platform scale and ownership", state: "Needs Validation" }),
    "Needs Validation: Platform scale and ownership",
  );
  assert.equal(
    gapText({ requirement_text: "Kafka production operations", state: "Not Found" }),
    "Evidence not found for Kafka production operations",
  );
});

test("parseRevisionParam accepts a positive integer string", () => {
  assert.equal(parseRevisionParam("2"), 2);
});

test("parseRevisionParam rejects trailing garbage, decimals, non-positive, array, and missing input", () => {
  assert.equal(parseRevisionParam("1abc"), undefined);
  assert.equal(parseRevisionParam("1.9"), undefined);
  assert.equal(parseRevisionParam("0"), undefined);
  assert.equal(parseRevisionParam("-1"), undefined);
  assert.equal(parseRevisionParam(["2", "3"]), 2);
  assert.equal(parseRevisionParam(undefined), undefined);
});

test("identityReferenceSuffix omits the reference when it duplicates the display name", () => {
  assert.equal(identityReferenceSuffix("D7K2", "D7K2"), null);
});

test("identityReferenceSuffix shows the reference when a parsed name differs from it", () => {
  assert.equal(identityReferenceSuffix("Jordan Lee", "P4M8"), "P4M8");
});
