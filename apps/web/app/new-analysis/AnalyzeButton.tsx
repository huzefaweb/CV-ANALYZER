"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toValidationSummaryItems, type AnalyzeValidationError } from "@/lib/analyzeValidationSummary";
import type { DocumentProjection } from "./DocumentUpload";
import type { Validation } from "./JobDescriptionForm";

// Story 3.4 (UX-DR7, UX-DR14): the Analyze button locks the exact prepared
// inputs. It does not track live session state itself beyond what its
// siblings report via callback — see NewAnalysisWorkspace.tsx.
export default function AnalyzeButton({
  sessionId,
  jobDescriptionVersion,
  jobDescriptionValidation,
  documents,
}: {
  sessionId: string;
  jobDescriptionVersion: number;
  jobDescriptionValidation: Validation;
  documents: DocumentProjection[];
}) {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [errors, setErrors] = useState<AnalyzeValidationError[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const summaryRef = useRef<HTMLDivElement>(null);

  const hasReadyDocument = documents.length > 0;
  const disabled = submitting || !jobDescriptionValidation.is_valid || !hasReadyDocument;

  async function handleClick() {
    setSubmitting(true);
    setErrors(null);
    setMessage(null);

    const idempotencyKey = crypto.randomUUID();
    const expectedDocumentVersions: Record<string, number> = {};
    for (const document of documents) expectedDocumentVersions[document.id] = document.content_version;

    let response: Response;
    try {
      response = await fetch("/api/new-analysis/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sessionId,
          expectedJobDescriptionVersion: jobDescriptionVersion,
          expectedDocumentVersions,
          idempotencyKey,
        }),
      });
    } catch {
      setSubmitting(false);
      setMessage("Unable to reach the server. Check your connection and try again.");
      return;
    }

    let body: unknown;
    try {
      body = await response.json();
    } catch {
      body = null;
    }

    if (response.status === 202) {
      // AC#1/#3: the session is now locked — reload the server-rendered
      // state so it reconstructs the locked view (the server projection is
      // the single source of truth, not a client-side guess).
      router.refresh();
      return;
    }

    if (response.status === 409 && body && typeof body === "object" && "errors" in body) {
      setSubmitting(false);
      setErrors((body as { errors: AnalyzeValidationError[] }).errors);
      // UX-DR14: focus the validation summary after an explicit failed
      // submission; inline field errors (Job Description's own validation
      // text) remain visible regardless.
      queueMicrotask(() => summaryRef.current?.focus());
      return;
    }

    if (
      response.status === 409 &&
      body &&
      typeof body === "object" &&
      "error" in body &&
      ((body as { error: unknown }).error === "active_preparation_exists" ||
        (body as { error: unknown }).error === "idempotency_key_conflict")
    ) {
      // A preparation already exists for this session — the correct next
      // state is the locked view, same as a fresh 202.
      router.refresh();
      return;
    }

    setSubmitting(false);
    setMessage("Analysis could not be started right now. Try again.");
  }

  const summaryItems = errors ? toValidationSummaryItems(errors) : [];

  return (
    <div className="panel">
      <div className="notice">
        <b>What freezes when analysis starts</b>
        <br />
        The Job Description, Ready Documents, Scoring Configuration, and version context become immutable for this
        Analysis Session. Rejected Documents will not enter the frozen cohort.
      </div>
      {errors && errors.length > 0 ? (
        <div ref={summaryRef} tabIndex={-1} role="alert" id="analyze-validation-summary" className="validation-summary">
          <h3>Analyze could not start. Review the following:</h3>
          <ul>
            {summaryItems.map((item, index) => (
              <li key={`${item.href}-${index}`}>
                <a href={item.href}>{item.label}</a>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {message ? (
        <p role="alert" aria-live="assertive">
          {message}
        </p>
      ) : null}
      <button type="button" disabled={disabled} aria-disabled={disabled} onClick={handleClick} aria-describedby="analyze-blocked">
        {submitting ? "Analyzing…" : "Analyze"}
      </button>
      {!jobDescriptionValidation.is_valid || !hasReadyDocument ? (
        <div className="notice" id="analyze-blocked">
          {!jobDescriptionValidation.is_valid
            ? `Enter at least ${jobDescriptionValidation.minimum_required} non-whitespace characters (currently ${jobDescriptionValidation.non_whitespace_count}) to enable Analyze.`
            : "Upload at least one Document to enable Analyze."}
        </div>
      ) : null}
    </div>
  );
}
