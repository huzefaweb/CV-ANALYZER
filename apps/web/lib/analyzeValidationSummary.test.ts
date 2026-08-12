import assert from "node:assert/strict";
import { test } from "node:test";
import { toValidationSummaryItems } from "./analyzeValidationSummary.ts";

test("a Job-Description-only error links to the job description field", () => {
  const items = toValidationSummaryItems([{ field: "job_description", reason: "below_minimum_length" }]);
  assert.deepEqual(items, [{ href: "#job-description", label: "Job Description is too short" }]);
});

test("a single Document error links to that Document's row", () => {
  const items = toValidationSummaryItems([{ field: "document:abc-123", reason: "stale_version" }]);
  assert.deepEqual(items, [{ href: "#document-abc-123", label: "A Document changed elsewhere" }]);
});

test("multiple mixed errors each produce their own linked entry", () => {
  const items = toValidationSummaryItems([
    { field: "job_description", reason: "stale_version" },
    { field: "document:abc-123", reason: "missing" },
    { field: "documents", reason: "none_ready" },
  ]);
  assert.deepEqual(items, [
    { href: "#job-description", label: "Job Description changed elsewhere" },
    { href: "#document-abc-123", label: "A Document is no longer part of this Analysis" },
    { href: "#document-upload", label: "Documents has no ready Documents" },
  ]);
});

test("zero errors produces an empty list", () => {
  assert.deepEqual(toValidationSummaryItems([]), []);
});
