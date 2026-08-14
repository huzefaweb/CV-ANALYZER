// Shared pure formatters for Results and Candidate Report (Story 5.2/6.1) —
// extracted from results/page.tsx once a second caller (candidates/[id]/
// report/page.tsx) needed the identical logic, rather than duplicating it.

export type EvidencePoint = { requirement_text: string; state: string };

export function shortlistLabel(state: string): string {
  return state === "Shortlisted" ? "Shortlisted" : "Not shortlisted";
}

// Story 6.4 (AC#3): Shortlist is Candidate-owned, not revision-owned — a
// retry that moves a previously ranked Candidate to Needs Review or Failed
// never clears an existing Shortlist mark. Shown next to the toggle
// wherever a Candidate isn't Ranked, so the Recruiter understands why a
// Shortlist mark on a Needs Review/Failed Candidate is still present and
// that it carries no scoring/rank/lifecycle meaning.
export const SHORTLIST_RETENTION_NOTE =
  "Shortlist reflects your own decision and is unaffected by outcome or scoring — it is retained even when a retry changes this Candidate's outcome.";

export function gapText(point: EvidencePoint): string {
  return point.state === "Needs Validation"
    ? `Needs Validation: ${point.requirement_text}`
    : `Evidence not found for ${point.requirement_text}`;
}

// UX-DR10: a parsed display name and the Document Reference fallback are
// often identical (no parsed name), in which case showing both is
// redundant ("D7K2 — D7K2") — the reference suffix appears only when it
// carries information the display name doesn't already show.
export function identityReferenceSuffix(displayName: string, documentReference: string): string | null {
  return displayName !== documentReference ? documentReference : null;
}

// Defensive parse (Story 5.4): a malformed `?revision=` never reaches the
// gateway query string — treated as absent, falling back to the current
// published revision, rather than forwarding garbage for the gateway to
// reject.
export function parseRevisionParam(raw: string | string[] | undefined): number | undefined {
  const value = Array.isArray(raw) ? raw[0] : raw;
  // Review finding (Story 5.4): Number.parseInt is lenient about trailing
  // garbage ("1abc" -> 1, "1.9" -> 1) — require the whole string to be
  // digits before parsing, so malformed input is genuinely treated as absent.
  if (value === undefined || !/^[0-9]+$/.test(value)) return undefined;
  const parsed = Number.parseInt(value, 10);
  return parsed > 0 ? parsed : undefined;
}
