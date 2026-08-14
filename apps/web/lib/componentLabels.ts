// Plain-language display labels for the gateway's rubric Component wire
// values (apps/gateway/src/domain/scoring_configuration.py::Component).
// Verbatim canonical labels, epics.md's rubric-component naming.

const COMPONENT_LABELS: Record<string, string> = {
  mandatory_skills: "Mandatory skills",
  relevant_experience: "Relevant experience",
  responsibility_alignment: "Responsibility alignment",
  preferred_skills_tools: "Preferred skills/tools",
  education_certifications: "Education/certifications",
  domain_fit: "Domain fit",
  achievement_evidence_quality: "Achievement/Evidence quality",
};

export function componentLabel(component: unknown): string {
  if (typeof component === "string" && component in COMPONENT_LABELS) return COMPONENT_LABELS[component];
  return "Unrecognized component";
}
