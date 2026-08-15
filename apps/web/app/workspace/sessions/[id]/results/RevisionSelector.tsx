"use client";

import { useRouter } from "next/navigation";
import type { ChangeEvent } from "react";
import { revisionOptionLabel, type RevisionOption } from "@/lib/revisionSelectorLabel";

export type { RevisionOption };

export default function RevisionSelector({
  sessionId,
  revisions,
  activeRevisionNumber,
  hrefForPublished,
}: {
  sessionId: string;
  revisions: RevisionOption[];
  activeRevisionNumber: number | null;
  // Candidate Report reuses this same selector but must stay on the
  // Candidate's own report at the chosen revision, not jump to Results —
  // defaults to the original Results-page destination unchanged.
  hrefForPublished?: (revision: RevisionOption) => string;
}) {
  const router = useRouter();

  // UX-DR22: selection navigates only after a committed choice — native
  // `<select>` onChange only fires after the browser commits a value, never
  // on keystroke-level input. Next.js's router supersedes an in-flight
  // navigation with a newer one natively (AC#3's "stale selections cannot
  // replace a newer chosen revision") — no custom sequencing needed here.
  function handleChange(event: ChangeEvent<HTMLSelectElement>) {
    const selected = revisions.find((r) => String(r.revision_number) === event.target.value);
    if (!selected) return;
    if (selected.published) {
      router.push(hrefForPublished ? hrefForPublished(selected) : `/workspace/sessions/${sessionId}/results?revision=${selected.revision_number}`);
    } else {
      router.push(`/workspace/sessions/${sessionId}`);
    }
  }

  return (
    <div className="revision-selector">
      <label htmlFor="revision-selector-input">Analysis revision</label>
      <select id="revision-selector-input" value={activeRevisionNumber ?? ""} onChange={handleChange}>
        {activeRevisionNumber === null ? (
          // Review finding: a controlled `<select>`'s value must match a
          // real `<option>` — without this, the browser silently falls
          // back to the first option while React's state stays desynced
          // from what's visually selected.
          <option value="" disabled>
            Not yet published
          </option>
        ) : null}
        {revisions.map((revision) => (
          <option key={revision.revision_number} value={revision.revision_number}>
            {revisionOptionLabel(revision)}
          </option>
        ))}
      </select>
    </div>
  );
}
