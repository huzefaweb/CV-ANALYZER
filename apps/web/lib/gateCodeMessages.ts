// Plain-language translation of the gateway's Needs Review scoreability
// gate codes (candidate_results.gate_codes, apps/gateway/src/domain/
// scoring.py / AD-11) for display on Ranked Results Needs Review rows.
// EXPERIENCE.md's "Needs Review and retry eligibility" section (lines
// 268-271) is the copy source, quoted verbatim.

const GATE_CODE_MESSAGES: Record<string, string> = {
  PARSE_FATAL: "Reliable text could not be extracted from this Resume.",
  TEXT_BELOW_500: "Fewer than 500 characters of readable content were found.",
  COHERENT_BLOCKS_BELOW_2: "Fewer than two coherent Resume sections were found.",
  COVERAGE_BELOW_7000_BPS: "Evidence covered less than 7,000 of 10,000 required basis points.",
};

export function gateCodeMessage(code: unknown): string {
  if (typeof code === "string" && code in GATE_CODE_MESSAGES) return GATE_CODE_MESSAGES[code];
  return "This Resume could not be scored.";
}
