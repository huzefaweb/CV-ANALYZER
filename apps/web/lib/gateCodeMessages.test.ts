import assert from "node:assert/strict";
import { test } from "node:test";
import { gateCodeMessage } from "./gateCodeMessages.ts";

test("translates every known gateway gate code to a plain-language message", () => {
  assert.equal(gateCodeMessage("TEXT_BELOW_500"), "Fewer than 500 characters of readable content were found.");
  assert.equal(
    gateCodeMessage("COVERAGE_BELOW_7000_BPS"),
    "Evidence covered less than 7,000 of 10,000 required basis points.",
  );
});

test("falls back to a generic message for an unknown code", () => {
  assert.equal(gateCodeMessage("SOME_FUTURE_CODE"), "This Resume could not be scored.");
});

test("falls back to a generic message for a non-string or missing code", () => {
  assert.equal(gateCodeMessage(undefined), "This Resume could not be scored.");
  assert.equal(gateCodeMessage(null), "This Resume could not be scored.");
  assert.equal(gateCodeMessage(42), "This Resume could not be scored.");
});
