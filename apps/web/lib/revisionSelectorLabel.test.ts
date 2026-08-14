import assert from "node:assert/strict";
import { test } from "node:test";
import { revisionOptionLabel } from "./revisionSelectorLabel.ts";

test("nonpublished revision reads as Processing", () => {
  assert.equal(
    revisionOptionLabel({ revision_number: 2, published: false, published_at: null, is_current: false }),
    "Revision 2 — Processing",
  );
});

test("current published revision reads as (current)", () => {
  assert.equal(
    revisionOptionLabel({ revision_number: 2, published: true, published_at: "2026-01-01T00:00:00Z", is_current: true }),
    "Revision 2 — Published (current)",
  );
});

test("older published revision reads as (previous)", () => {
  assert.equal(
    revisionOptionLabel({ revision_number: 1, published: true, published_at: "2026-01-01T00:00:00Z", is_current: false }),
    "Revision 1 — Published (previous)",
  );
});
