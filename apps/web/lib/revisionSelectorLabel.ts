// UX-DR22/UX-DR4: revision selector option text — full labels, not color,
// carry the lifecycle/relationship meaning (Story 5.4).

export type RevisionOption = {
  revision_number: number;
  published: boolean;
  published_at: string | null;
  is_current: boolean;
};

export function revisionOptionLabel(revision: RevisionOption): string {
  if (!revision.published) return `Revision ${revision.revision_number} — Processing`;
  return revision.is_current
    ? `Revision ${revision.revision_number} — Published (current)`
    : `Revision ${revision.revision_number} — Published (previous)`;
}
