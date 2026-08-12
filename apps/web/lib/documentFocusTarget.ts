// Pure focus-target resolution for Document row removal (UX EXPERIENCE.md
// "Row removal" rule): after removing a row, focus moves to the next row,
// otherwise the previous row, otherwise the upload control (null).

export function nextFocusTarget(visibleKeys: string[], removedKey: string): string | null {
  const index = visibleKeys.indexOf(removedKey);
  if (index === -1) return null;

  const remaining = visibleKeys.filter((key) => key !== removedKey);
  if (remaining.length === 0) return null;
  if (index < remaining.length) return remaining[index];
  return remaining[remaining.length - 1];
}
