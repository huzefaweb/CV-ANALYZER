import assert from "node:assert/strict";
import { test } from "node:test";
import { componentLabel } from "./componentLabels.ts";

test("translates every known rubric component to a plain-language label", () => {
  assert.equal(componentLabel("mandatory_skills"), "Mandatory skills");
  assert.equal(componentLabel("relevant_experience"), "Relevant experience");
  assert.equal(componentLabel("responsibility_alignment"), "Responsibility alignment");
  assert.equal(componentLabel("preferred_skills_tools"), "Preferred skills/tools");
  assert.equal(componentLabel("education_certifications"), "Education/certifications");
  assert.equal(componentLabel("domain_fit"), "Domain fit");
  assert.equal(componentLabel("achievement_evidence_quality"), "Achievement/Evidence quality");
});

test("falls back to a generic label for an unrecognized component", () => {
  assert.equal(componentLabel("some_future_component"), "Unrecognized component");
  assert.equal(componentLabel(undefined), "Unrecognized component");
  assert.equal(componentLabel(42), "Unrecognized component");
});
