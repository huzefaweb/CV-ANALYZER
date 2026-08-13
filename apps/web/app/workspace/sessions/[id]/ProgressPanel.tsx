"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { createPollSequencer } from "@/lib/pollSequencer";

// Story 4.7 (AC#2): safely above the "no faster than 2 seconds" client-poll
// floor — a flat margin, not a spec-given number (see story Dev Notes).
const POLL_INTERVAL_MS = 2500;

type ProgressRow = { candidate_id: string; document_reference: string; state: string };

type ProgressSnapshot = {
  revision_number: number | null;
  rows: ProgressRow[];
  aggregate: { total: number; by_state: Record<string, number> };
  all_terminal: boolean;
  ranking_suppressed: boolean;
  view_results_available: boolean;
};

const EMPTY_SNAPSHOT: ProgressSnapshot = {
  revision_number: null,
  rows: [],
  aggregate: { total: 0, by_state: {} },
  all_terminal: false,
  ranking_suppressed: false,
  view_results_available: false,
};

function summarize(snapshot: ProgressSnapshot): string {
  if (snapshot.rows.length === 0) return "No Documents queued yet.";
  const parts = Object.entries(snapshot.aggregate.by_state).map(([state, count]) => `${count} ${state}`);
  return `${snapshot.aggregate.total} total — ${parts.join(", ")}.`;
}

export default function ProgressPanel({ sessionId }: { sessionId: string }) {
  const router = useRouter();
  const [snapshot, setSnapshot] = useState<ProgressSnapshot>(EMPTY_SNAPSHOT);
  // AD-3/UX-DR9 (review finding): reuse the exact same neutral 404 copy
  // `page.tsx` renders on first load, distinct from the 401/403 redirects —
  // a poll must collapse to the same treatment as the initial page request,
  // not invent a fourth, different denied-access surface.
  const [deniedNotFound, setDeniedNotFound] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const sequencerRef = useRef(createPollSequencer());
  const lastAnnouncedRef = useRef<string>("");
  const [announcement, setAnnouncement] = useState("");
  // A poll in flight when access is revoked must not schedule another one —
  // read synchronously inside the closure rather than depending on React
  // state, which would not be visible yet inside an already-started poll().
  const stoppedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    stoppedRef.current = false;

    async function poll() {
      if (stoppedRef.current) return;
      const requestId = sequencerRef.current.issue();
      let response: Response;
      try {
        response = await fetch(`/api/workspace/sessions/${encodeURIComponent(sessionId)}/progress`);
      } catch {
        if (!cancelled && sequencerRef.current.isCurrent(requestId)) {
          setReconnecting(true);
          setAnnouncement("Reconnecting…");
        }
        return;
      }
      if (cancelled || !sequencerRef.current.isCurrent(requestId)) return;

      // AC#2's "clears data on denied access" — stop polling entirely
      // rather than continuing to hammer a session that can only recover
      // via a fresh login/page load (review finding).
      if (response.status === 401 || response.status === 403 || response.status === 404) {
        stoppedRef.current = true;
        setSnapshot(EMPTY_SNAPSHOT);
        if (response.status === 401) {
          router.push(`/session-expired?return_to=${encodeURIComponent(`/workspace/sessions/${sessionId}`)}`);
          return;
        }
        if (response.status === 403) {
          router.push("/login");
          return;
        }
        setDeniedNotFound(true);
        return;
      }
      if (!response.ok) {
        setReconnecting(true);
        setAnnouncement("Reconnecting…");
        return;
      }

      let data: ProgressSnapshot;
      try {
        data = await response.json();
      } catch {
        setReconnecting(true);
        setAnnouncement("Reconnecting…");
        return;
      }
      if (cancelled || !sequencerRef.current.isCurrent(requestId)) return;

      setReconnecting(false);
      // AC#2: one complete authorized persisted snapshot replaces transient
      // UI state — never merged/diffed against the previous one.
      setSnapshot(data);

      const key = `${data.aggregate.total}:${JSON.stringify(data.aggregate.by_state)}:${data.all_terminal}`;
      if (key !== lastAnnouncedRef.current) {
        lastAnnouncedRef.current = key;
        setAnnouncement(summarize(data));
      }
    }

    poll();
    const interval = setInterval(() => {
      if (stoppedRef.current) {
        clearInterval(interval);
        return;
      }
      poll();
    }, POLL_INTERVAL_MS);

    function onReconnectSignal() {
      if (!stoppedRef.current) poll();
    }
    document.addEventListener("visibilitychange", onReconnectSignal);
    window.addEventListener("online", onReconnectSignal);

    return () => {
      cancelled = true;
      stoppedRef.current = true;
      clearInterval(interval);
      document.removeEventListener("visibilitychange", onReconnectSignal);
      window.removeEventListener("online", onReconnectSignal);
    };
  }, [sessionId, router]);

  if (deniedNotFound) {
    return (
      <main id="main">
        <h1>Authorization denied</h1>
        <p>This isn&apos;t available.</p>
        <p>
          <a href="/workspace">Return to workspace</a>
        </p>
      </main>
    );
  }

  return (
    <div className="panel">
      <h2>Progress</h2>
      {/* One shared live region (AC#2: "announced once without moving
          focus") — reconnect status and meaningful snapshot changes share
          the same announcement text rather than two independently
          announcing regions. */}
      <p aria-live="polite">{reconnecting ? "Reconnecting…" : announcement}</p>
      {snapshot.rows.length === 0 ? (
        <p>No Documents queued yet.</p>
      ) : (
        <ul>
          {snapshot.rows.map((row) => (
            <li key={row.candidate_id}>
              {row.document_reference}: {row.state}
            </li>
          ))}
        </ul>
      )}
      {snapshot.ranking_suppressed ? (
        <p>
          All Documents have reached a final outcome. Ranking is suppressed until publication completes — this page
          will not navigate automatically.
        </p>
      ) : null}
    </div>
  );
}
