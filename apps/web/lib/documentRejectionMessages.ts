// Plain-language translation of the gateway's document-intake rejection
// categories (src/domain/document_intake.py::RejectionCategory) for
// display in DocumentUpload.tsx's rejected rows.

const REJECTION_MESSAGES: Record<string, string> = {
  count_exceeded: "The 20-Document limit has been reached.",
  size_limit: "This file is larger than 10 MB.",
  extension_rejected: "Only PDF or DOCX files are accepted.",
  signature_mismatch: "This file's contents don't match a PDF or DOCX file.",
  password_protected: "This file is password-protected.",
  corrupt_container: "This file could not be read — it may be corrupt.",
  archive_expansion: "This file expands to an unsafe size and was rejected.",
  conflict: "This file could not be saved right now. Try again.",
  invalid_request: "This file could not be uploaded.",
};

export function rejectionMessage(category: unknown): string {
  if (typeof category === "string" && category in REJECTION_MESSAGES) return REJECTION_MESSAGES[category];
  return "This file could not be uploaded.";
}
