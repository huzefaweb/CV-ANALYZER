"use client";

import { useEffect, useRef, useState, type DragEvent } from "react";
import { rejectionMessage } from "@/lib/documentRejectionMessages";
import { nextFocusTarget } from "@/lib/documentFocusTarget";

export type DocumentProjection = {
  id: string;
  document_reference: string;
  original_filename: string;
  content_version: number;
  size_bytes: number;
  content_type: string;
  status: string;
  created_at: string;
};

type Row =
  | { key: string; state: "pending"; filename: string }
  | { key: string; state: "ready"; document: DocumentProjection; message?: string }
  | { key: string; state: "replacing"; document: DocumentProjection }
  | { key: string; state: "rejected"; filename: string; reason: string };

const MAX_DOCUMENT_COUNT = 20;

export default function DocumentUpload({
  sessionId,
  initialDocuments,
  onDocumentsChange,
}: {
  sessionId: string;
  initialDocuments: DocumentProjection[];
  onDocumentsChange?: (documents: DocumentProjection[]) => void;
}) {
  const [rows, setRows] = useState<Row[]>(
    initialDocuments.map((document) => ({ key: document.id, state: "ready", document }))
  );

  // Analyze (Story 3.4) needs the live Ready-Document set to build its
  // expected_document_versions snapshot — reported via callback rather than
  // lifting `rows` itself, so this component's internal row-state shape
  // (pending/replacing/rejected) stays private to it.
  useEffect(() => {
    onDocumentsChange?.(
      rows.filter((row): row is Extract<Row, { state: "ready" }> => row.state === "ready").map((row) => row.document)
    );
  }, [rows, onDocumentsChange]);
  const [busyKeys, setBusyKeys] = useState<Set<string>>(new Set());
  const inputRef = useRef<HTMLInputElement>(null);
  const replaceInputRef = useRef<HTMLInputElement>(null);
  // Focus targets are `${rowKey}:remove` / `${rowKey}:replace`, or null for
  // the upload control — a compound key since a row can have two actions.
  const focusTargetRef = useRef<string | null | undefined>(undefined);
  const actionRefs = useRef<Map<string, HTMLElement>>(new Map());
  const readyCount = rows.filter((row) => row.state === "ready" || row.state === "replacing").length;
  const atCapacity = readyCount >= MAX_DOCUMENT_COUNT;

  // AC#2/AC#3 / UX "Row removal" rule: after a row leaves the DOM or a
  // replace attempt resolves, move focus to the resolved target.
  useEffect(() => {
    const target = focusTargetRef.current;
    if (target === undefined) return;
    focusTargetRef.current = undefined;
    if (target === null) {
      inputRef.current?.focus();
    } else {
      actionRefs.current.get(target)?.focus();
    }
  }, [rows]);

  // The file input 'cancel' event has no typed React prop — attach it
  // directly. Clears busy state when the user dismisses the OS file picker
  // without choosing a replacement, so the Replace/Remove buttons re-enable.
  useEffect(() => {
    const input = replaceInputRef.current;
    if (!input) return;
    const onCancel = (event: Event) => {
      const rowKey = (event.currentTarget as HTMLInputElement).dataset.rowKey;
      if (rowKey) setBusy(rowKey, false);
    };
    input.addEventListener("cancel", onCancel);
    return () => input.removeEventListener("cancel", onCancel);
  }, []);

  function setBusy(key: string, busy: boolean) {
    setBusyKeys((prev) => {
      const next = new Set(prev);
      if (busy) next.add(key);
      else next.delete(key);
      return next;
    });
  }

  async function handleFiles(fileList: FileList) {
    // Sequential, in the browser's given picker/drop order — each call
    // re-checks the server-side count, which is what makes "accept up to
    // remaining capacity, reject only the remainder" correct.
    for (const file of Array.from(fileList)) {
      const key = crypto.randomUUID();
      setRows((prev) => [...prev, { key, state: "pending", filename: file.name }]);

      const formData = new FormData();
      formData.set("sessionId", sessionId);
      formData.set("file", file);
      formData.set("idempotencyKey", key);

      let response: Response;
      try {
        response = await fetch("/api/new-analysis/documents", { method: "POST", body: formData });
      } catch {
        setRows((prev) =>
          prev.map((row) =>
            row.key === key ? { key, state: "rejected", filename: file.name, reason: "Unable to reach the server." } : row
          )
        );
        continue;
      }

      let body: unknown;
      try {
        body = await response.json();
      } catch {
        body = null;
      }

      if ((response.status === 201 || response.status === 200) && isDocumentProjection(body)) {
        setRows((prev) => prev.map((row) => (row.key === key ? { key, state: "ready", document: body } : row)));
        continue;
      }

      const category = body && typeof body === "object" && "category" in body ? (body as { category: unknown }).category : undefined;
      setRows((prev) =>
        prev.map((row) => (row.key === key ? { key, state: "rejected", filename: file.name, reason: rejectionMessage(category) } : row))
      );
    }
  }

  function onInputChange(event: React.ChangeEvent<HTMLInputElement>) {
    if (event.target.files && event.target.files.length > 0) handleFiles(event.target.files);
    event.target.value = "";
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    if (atCapacity) return;
    if (event.dataTransfer.files.length > 0) handleFiles(event.dataTransfer.files);
  }

  function onDragOver(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
  }

  async function handleRemove(row: Extract<Row, { state: "ready" }>) {
    setBusy(row.key, true);
    const idempotencyKey = crypto.randomUUID();
    // Only `ready` rows have a registered action ref (a `replacing` row
    // renders no buttons) — restrict the candidate set to what the focus
    // effect can actually find, or the resolved target silently gets no
    // focus at all.
    const visibleKeys = rows.filter((r) => r.state === "ready").map((r) => r.key);

    let response: Response;
    try {
      response = await fetch(`/api/new-analysis/documents/${encodeURIComponent(row.document.id)}/remove`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sessionId, expectedVersion: row.document.content_version, idempotencyKey }),
      });
    } catch {
      setBusy(row.key, false);
      setRows((prev) =>
        prev.map((r) => (r.key === row.key && r.state === "ready" ? { ...r, message: "Unable to reach the server." } : r))
      );
      return;
    }

    let body: unknown;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    setBusy(row.key, false);

    if (response.status === 200 && isDocumentProjection(body) && body.status === "removed") {
      const next = nextFocusTarget(visibleKeys, row.key);
      focusTargetRef.current = next ? `${next}:remove` : null;
      setRows((prev) => prev.filter((r) => r.key !== row.key));
      return;
    }

    if (response.status === 409 && isDocumentProjection(body)) {
      if (body.status === "removed") {
        // Lost a race to a concurrent Remove — the row is gone either way.
        const next = nextFocusTarget(visibleKeys, row.key);
        focusTargetRef.current = next ? `${next}:remove` : null;
        setRows((prev) => prev.filter((r) => r.key !== row.key));
        return;
      }
      // AC#2: stale version — the prior valid Document remains current,
      // shown with an inline recovery message; focus stays on the row.
      setRows((prev) =>
        prev.map((r) => (r.key === row.key ? { key: row.key, state: "ready", document: body, message: "This Document changed elsewhere. Try again if you still want to remove it." } : r))
      );
      return;
    }

    setRows((prev) =>
      prev.map((r) => (r.key === row.key && r.state === "ready" ? { ...r, message: "This Document could not be removed. Try again." } : r))
    );
  }

  function triggerReplace(row: Extract<Row, { state: "ready" }>) {
    if (!replaceInputRef.current) return;
    // Mark this row busy immediately (not only once a file is chosen) so a
    // second Replace click — on this row or another — cannot re-target the
    // one shared hidden input before the OS file dialog resolves.
    setBusy(row.key, true);
    replaceInputRef.current.dataset.documentId = row.document.id;
    replaceInputRef.current.dataset.rowKey = row.key;
    replaceInputRef.current.click();
  }

  async function onReplaceInputChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    const documentId = event.target.dataset.documentId;
    const rowKey = event.target.dataset.rowKey;
    event.target.value = "";
    if (!file || !documentId || !rowKey) {
      if (rowKey) setBusy(rowKey, false);
      return;
    }

    const current = rows.find((r) => r.key === rowKey);
    if (!current || current.state !== "ready") {
      setBusy(rowKey, false);
      return;
    }

    const previousDocument = current.document;
    setRows((prev) => prev.map((r) => (r.key === rowKey ? { key: rowKey, state: "replacing", document: previousDocument } : r)));

    const idempotencyKey = crypto.randomUUID();
    const formData = new FormData();
    formData.set("sessionId", sessionId);
    formData.set("file", file);
    formData.set("expectedVersion", String(previousDocument.content_version));
    formData.set("idempotencyKey", idempotencyKey);

    let response: Response;
    try {
      response = await fetch(`/api/new-analysis/documents/${encodeURIComponent(documentId)}`, { method: "PUT", body: formData });
    } catch {
      setBusy(rowKey, false);
      focusTargetRef.current = `${rowKey}:replace`;
      setRows((prev) => prev.map((r) => (r.key === rowKey ? { key: rowKey, state: "ready", document: previousDocument, message: "Unable to reach the server." } : r)));
      return;
    }

    let body: unknown;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    setBusy(rowKey, false);

    if (response.status === 200 && isDocumentProjection(body) && body.status === "ready") {
      setRows((prev) => prev.map((r) => (r.key === rowKey ? { key: rowKey, state: "ready", document: body } : r)));
      return;
    }

    if (response.status === 409 && isDocumentProjection(body)) {
      if (body.status === "removed") {
        setRows((prev) => prev.filter((r) => r.key !== rowKey));
        return;
      }
      focusTargetRef.current = `${rowKey}:replace`;
      setRows((prev) => prev.map((r) => (r.key === rowKey ? { key: rowKey, state: "ready", document: body, message: "This Document changed elsewhere. The current version is shown." } : r)));
      return;
    }

    // 422 (rejected) or any other failure: revert to the prior valid
    // Document — AC#2's "the prior valid Document remains current" — and
    // return focus to this row's Replace button (AC#2, explicit).
    const category = body && typeof body === "object" && "category" in body ? (body as { category: unknown }).category : undefined;
    focusTargetRef.current = `${rowKey}:replace`;
    setRows((prev) =>
      prev.map((r) => (r.key === rowKey ? { key: rowKey, state: "ready", document: previousDocument, message: rejectionMessage(category) } : r))
    );
  }

  return (
    <div>
      <h2>Documents</h2>
      <p id="document-upload-guidance">
        Up to 20 PDF or DOCX files, 10 MB each. {MAX_DOCUMENT_COUNT - readyCount} remaining.
      </p>
      <div onDrop={onDrop} onDragOver={onDragOver}>
        <label htmlFor="document-upload">Select or drop Resume files</label>
        <input
          ref={inputRef}
          id="document-upload"
          type="file"
          multiple
          accept=".pdf,.docx"
          onChange={onInputChange}
          aria-describedby="document-upload-guidance"
          disabled={atCapacity}
        />
        {atCapacity ? <p role="status">The 20-Document limit has been reached.</p> : null}
      </div>
      <input
        ref={replaceInputRef}
        type="file"
        accept=".pdf,.docx"
        onChange={onReplaceInputChange}
        style={{ display: "none" }}
        aria-hidden="true"
        tabIndex={-1}
      />
      <ul aria-live="polite">
        {rows.map((row) => (
          <li key={row.key}>
            {row.state === "pending" ? <span>{row.filename}: uploading…</span> : null}
            {row.state === "replacing" ? (
              <span>
                {row.document.document_reference} — {row.document.original_filename} — Replacing…
              </span>
            ) : null}
            {row.state === "ready" ? (
              <span id={`document-${row.document.id}`}>
                <span aria-label={`${row.document.document_reference}, ${row.document.original_filename}, Ready`}>
                  {row.document.document_reference} — {row.document.original_filename} — Ready
                </span>
                {row.message ? (
                  <span role="alert"> {row.message}</span>
                ) : null}
                <button
                  type="button"
                  ref={(el) => {
                    const refKey = `${row.key}:remove`;
                    if (el) actionRefs.current.set(refKey, el);
                    else actionRefs.current.delete(refKey);
                  }}
                  aria-label={`Remove ${row.document.document_reference}`}
                  disabled={busyKeys.has(row.key)}
                  onClick={() => handleRemove(row)}
                >
                  Remove
                </button>
                <button
                  type="button"
                  ref={(el) => {
                    const refKey = `${row.key}:replace`;
                    if (el) actionRefs.current.set(refKey, el);
                    else actionRefs.current.delete(refKey);
                  }}
                  aria-label={`Replace ${row.document.document_reference}`}
                  disabled={busyKeys.has(row.key)}
                  onClick={() => triggerReplace(row)}
                >
                  Replace
                </button>
              </span>
            ) : null}
            {row.state === "rejected" ? (
              <span role="alert">
                {row.filename} — Rejected — {row.reason}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function isDocumentProjection(body: unknown): body is DocumentProjection {
  return (
    typeof body === "object" &&
    body !== null &&
    "id" in body &&
    "document_reference" in body &&
    "original_filename" in body &&
    "status" in body
  );
}
