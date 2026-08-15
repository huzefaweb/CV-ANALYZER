// Story 7.4/AC#2: decides the print-readiness state PrintInvoke.tsx renders
// from a font-loading race outcome. Kept pure/testable; the browser-API race
// itself (document.fonts.ready vs. a timeout) lives in the client component.

export type PrintReadinessOutcome = "resolved" | "timeout" | "rejected";
export type PrintReadinessState = "ready" | "failed";

export function derivePrintReadinessState(outcome: PrintReadinessOutcome): PrintReadinessState {
  return outcome === "resolved" ? "ready" : "failed";
}
