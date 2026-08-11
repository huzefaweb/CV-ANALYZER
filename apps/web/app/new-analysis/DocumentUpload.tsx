"use client";

import { useRef, useState, type DragEvent } from "react";
import { rejectionMessage } from "@/lib/documentRejectionMessages";

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
  | { key: string; state: "ready"; document: DocumentProjection }
  | { key: string; state: "rejected"; filename: string; reason: string };

const MAX_DOCUMENT_COUNT = 20;

export default function DocumentUpload({
  sessionId,
  initialDocuments,
}: {
  sessionId: string;
  initialDocuments: DocumentProjection[];
}) {
  const [rows, setRows] = useState<Row[]>(
    initialDocuments.map((document) => ({ key: document.id, state: "ready", document }))
  );
  const inputRef = useRef<HTMLInputElement>(null);
  const readyCount = rows.filter((row) => row.state === "ready").length;
  const atCapacity = readyCount >= MAX_DOCUMENT_COUNT;

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
      <ul aria-live="polite">
        {rows.map((row) => (
          <li key={row.key}>
            {row.state === "pending" ? <span>{row.filename}: uploading…</span> : null}
            {row.state === "ready" ? (
              <span>
                {row.document.document_reference} — {row.document.original_filename} — Ready
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
