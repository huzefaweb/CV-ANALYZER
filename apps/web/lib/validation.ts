// Story 3.1 / AF-2: mirrors the gateway's frozen content-fixture rule
// (`apps/gateway/src/adapters/new_analysis.py::_validate_job_description`)
// for live client-side feedback. The gateway's response after a save
// remains the authoritative validation result — this is a display-only
// preview, not a submission gate.

export const MINIMUM_NON_WHITESPACE_CHARACTERS = 200;

export function countNonWhitespaceCharacters(text: string): number {
  return text.replace(/\s/g, "").length;
}

export function isJobDescriptionContentValid(text: string): boolean {
  return countNonWhitespaceCharacters(text) >= MINIMUM_NON_WHITESPACE_CHARACTERS;
}
