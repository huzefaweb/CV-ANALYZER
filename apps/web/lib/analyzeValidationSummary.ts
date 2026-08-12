export type AnalyzeValidationError = { field: string; reason: string };

export type ValidationSummaryItem = { href: string; label: string };

const REASON_LABELS: Record<string, string> = {
  below_minimum_length: "is too short",
  stale_version: "changed elsewhere",
  none_ready: "has no ready Documents",
  missing: "is no longer part of this Analysis",
  unexpected: "is no longer available",
};

// Story 3.4 (UX-DR14): maps the gateway's {field, reason} error shape to a
// validation-summary entry that links to the affected field/Document.
// `field` is either "job_description", "documents", or "document:<id>".
export function toValidationSummaryItems(errors: AnalyzeValidationError[]): ValidationSummaryItem[] {
  return errors.map((error) => {
    const reasonLabel = REASON_LABELS[error.reason] ?? "is invalid";
    if (error.field === "job_description") {
      return { href: "#job-description", label: `Job Description ${reasonLabel}` };
    }
    if (error.field === "documents") {
      return { href: "#document-upload", label: `Documents ${reasonLabel}` };
    }
    const documentId = error.field.startsWith("document:") ? error.field.slice("document:".length) : error.field;
    return { href: `#document-${documentId}`, label: `A Document ${reasonLabel}` };
  });
}
