"use client";

import { useState } from "react";
import JobDescriptionForm, { type Validation } from "./JobDescriptionForm";
import DocumentUpload, { type DocumentProjection } from "./DocumentUpload";
import AnalyzeButton from "./AnalyzeButton";

// Story 3.4: Analyze needs the live Job Description version/validity and
// the live Ready-Document set, both of which are otherwise private state
// inside JobDescriptionForm/DocumentUpload. This thin client wrapper lifts
// only those two values via callback, so a page load's initial props stay
// server-fetched exactly as Story 3.1/3.2 built them.
export default function NewAnalysisWorkspace({
  sessionId,
  initialJobDescriptionText,
  initialJobDescriptionVersion,
  initialValidation,
  initialDocuments,
}: {
  sessionId: string;
  initialJobDescriptionText: string;
  initialJobDescriptionVersion: number;
  initialValidation: Validation;
  initialDocuments: DocumentProjection[];
}) {
  const [jobDescriptionVersion, setJobDescriptionVersion] = useState(initialJobDescriptionVersion);
  const [jobDescriptionValidation, setJobDescriptionValidation] = useState(initialValidation);
  const [documents, setDocuments] = useState<DocumentProjection[]>(initialDocuments);

  return (
    <>
      <div className="work">
        <JobDescriptionForm
          sessionId={sessionId}
          initialText={initialJobDescriptionText}
          initialVersion={initialJobDescriptionVersion}
          initialValidation={initialValidation}
          onSaved={({ version, validation }) => {
            setJobDescriptionVersion(version);
            setJobDescriptionValidation(validation);
          }}
        />
        <DocumentUpload sessionId={sessionId} initialDocuments={initialDocuments} onDocumentsChange={setDocuments} />
      </div>
      <AnalyzeButton
        sessionId={sessionId}
        jobDescriptionVersion={jobDescriptionVersion}
        jobDescriptionValidation={jobDescriptionValidation}
        documents={documents}
      />
    </>
  );
}
