"use client";

import { useEffect, useState } from "react";
import { derivePrintReadinessState, type PrintReadinessOutcome } from "@/lib/printReadiness";

type State = "checking" | "ready" | "failed";

const READINESS_TIMEOUT_MS = 4000;

function checkFontsReady(): Promise<PrintReadinessOutcome> {
  // Code review finding (Edge Case Hunter): `document.fonts` can be truthy
  // without a `.ready` promise on it — optional-chain the access instead of
  // assuming the full Font Loading API is present.
  if (typeof document === "undefined" || !document.fonts?.ready) return Promise.resolve("resolved");
  return new Promise<PrintReadinessOutcome>((resolve) => {
    // Code review finding (Blind Hunter): Promise.race doesn't cancel the
    // losing side — clear the timeout once fonts resolve first so it
    // doesn't linger after every check/retry.
    const timeoutId = setTimeout(() => resolve("timeout"), READINESS_TIMEOUT_MS);
    document.fonts.ready.then(() => {
      clearTimeout(timeoutId);
      resolve("resolved");
    });
  }).catch(() => "rejected");
}

// AC#2/AC#3: gates window.print() behind static content/fonts readiness and
// gives a neutral failure + safe retry — mirrors DestinationConfirmationGate
// .tsx's minimal untested-client-component shape (no component test file;
// the decision logic it calls, printReadiness.ts, is what's unit-tested).
export default function PrintInvoke() {
  const [state, setState] = useState<State>("checking");
  const [retryToken, setRetryToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState("checking");
    checkFontsReady().then((outcome) => {
      if (!cancelled) setState(derivePrintReadinessState(outcome));
    });
    return () => {
      cancelled = true;
    };
  }, [retryToken]);

  // Code review finding (Blind Hunter): window.print() can throw (e.g. a
  // sandboxed/embedding context that blocks it) — without a catch, the
  // click would fail silently with no feedback, unlike the readiness
  // path's own neutral-failure treatment.
  const handlePrint = () => {
    try {
      window.print();
    } catch {
      setState("failed");
    }
  };

  return (
    // Code review finding (Blind Hunter): the checking->ready transition
    // enables the real Print button with no announcement — aria-live
    // mirrors the accessible-state-transition scrutiny DestinationConfirmationGate.tsx
    // already applies to its own gated control (NFR-23).
    <div className="print-controls no-print" aria-live="polite">
      {state === "checking" ? (
        <>
          <p>Preparing print…</p>
          <button type="button" disabled aria-disabled="true">
            Print
          </button>
        </>
      ) : null}
      {state === "failed" ? (
        <>
          <p role="alert">Print preparation failed. The on-screen report is unaffected.</p>
          <button type="button" onClick={() => setRetryToken((token) => token + 1)}>
            Try again
          </button>
        </>
      ) : null}
      {state === "ready" ? (
        <button type="button" onClick={handlePrint}>
          Print
        </button>
      ) : null}
    </div>
  );
}
