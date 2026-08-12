"use client";

import { useState, type FormEvent } from "react";
import { countNonWhitespaceCharacters } from "@/lib/validation";

export type Validation = { non_whitespace_count: number; is_valid: boolean; minimum_required: number };

export default function JobDescriptionForm({
  sessionId,
  initialText,
  initialVersion,
  initialValidation,
  onSaved,
}: {
  sessionId: string;
  initialText: string;
  initialVersion: number;
  initialValidation: Validation;
  onSaved?: (saved: { version: number; validation: Validation }) => void;
}) {
  const [text, setText] = useState(initialText);
  const [version, setVersion] = useState(initialVersion);
  const [validation, setValidation] = useState<Validation>(initialValidation);
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [message, setMessage] = useState<string | null>(null);

  const liveCount = countNonWhitespaceCharacters(text);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStatus("saving");
    setMessage(null);

    let response: Response;
    try {
      response = await fetch("/api/new-analysis/job-description", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sessionId, jobDescriptionText: text, expectedVersion: version }),
      });
    } catch {
      setStatus("error");
      setMessage("Unable to reach the server. Check your connection and try again.");
      return;
    }

    let body: unknown;
    try {
      body = await response.json();
    } catch {
      setStatus("error");
      setMessage("The save could not be completed. Try again.");
      return;
    }

    if (response.status === 200 && isFullProjection(body)) {
      setText(body.job_description_text);
      setVersion(body.job_description_version);
      setValidation(body.validation);
      setStatus("saved");
      onSaved?.({ version: body.job_description_version, validation: body.validation });
      return;
    }

    if (response.status === 409 && isFullProjection(body)) {
      // AC#3: recoverable current projection — show what is actually saved,
      // never silently discard the Recruiter's rejected stale edit without
      // explanation.
      setText(body.job_description_text);
      setVersion(body.job_description_version);
      setValidation(body.validation);
      setStatus("error");
      setMessage(
        "This draft changed elsewhere. The current saved text is now shown — review and save again if you still want to make this change."
      );
      return;
    }

    if (response.status === 422) {
      // Pydantic's max_length rejection (NFR-9 request-size guard) — never
      // discard in-progress text, but give an actionable reason instead of
      // a generic failure.
      setStatus("error");
      setMessage("This Job Description is too long to save. Shorten it and try again.");
      return;
    }

    // Locked-session or other error shape: never discard in-progress text.
    setStatus("error");
    setMessage("This draft could not be saved right now.");
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <div>
        <label htmlFor="job-description">Job Description</label>
        <p id="job-description-guidance">
          Enter at least {validation.minimum_required} non-whitespace characters describing the role. Currently {liveCount}.
        </p>
        <textarea
          id="job-description"
          name="job-description"
          rows={16}
          value={text}
          onChange={(event) => setText(event.target.value)}
          aria-describedby="job-description-guidance"
        />
      </div>

      {message ? (
        <p role="alert" aria-live="assertive">
          {message}
        </p>
      ) : null}
      {status === "saved" ? (
        <p role="status" aria-live="polite">
          Saved.
        </p>
      ) : null}

      <button type="submit" disabled={status === "saving"}>
        {status === "saving" ? "Saving…" : "Save Job Description"}
      </button>
    </form>
  );
}

function isFullProjection(
  body: unknown
): body is { job_description_text: string; job_description_version: number; validation: Validation } {
  return (
    typeof body === "object" &&
    body !== null &&
    "job_description_version" in body &&
    "job_description_text" in body &&
    "validation" in body
  );
}
