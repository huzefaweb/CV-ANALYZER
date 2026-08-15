"use client";

import { useState } from "react";

// AC#1's "asks destination verification" — a plain unchecked-by-default
// checkbox gating the continue link is the lazy correct shape here (no new
// component library, mirrors ShortlistToggle.tsx's minimal client-component
// footprint for this app).
export default function DestinationConfirmationGate({ continuePath }: { continuePath: string }) {
  const [confirmed, setConfirmed] = useState(false);

  return (
    <div>
      <label>
        <input
          type="checkbox"
          checked={confirmed}
          onChange={(event) => setConfirmed(event.target.checked)}
        />{" "}
        I have verified my printer or PDF destination.
      </label>
      <p>
        {confirmed ? (
          <a href={continuePath}>Continue to print</a>
        ) : (
          // Code review fix (Blind Hunter): a bare `<span aria-disabled>` has
          // no interactive role and is not announced as a disabled control by
          // assistive technology. A real disabled `<button>` communicates the
          // gated state correctly (NFR-23).
          <button type="button" disabled aria-disabled="true">
            Continue to print
          </button>
        )}
      </p>
    </div>
  );
}
