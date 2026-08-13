// Story 4.7 (AC#2, NFR-16-18): guards a poll loop against stale, duplicate,
// or out-of-order responses without a server-side sequence token. `issue()`
// is called before each fetch; `isCurrent(id)` is checked before applying
// its response. The rule is "latest-issued wins," not "latest-to-arrive
// wins" — a response for an older request that resolves *after* a newer
// one (network reordering) is still correctly recognized as stale, because
// staleness is judged against issuance order, never arrival order.
export function createPollSequencer() {
  let latestIssuedId = 0;

  return {
    issue(): number {
      latestIssuedId += 1;
      return latestIssuedId;
    },
    isCurrent(id: number): boolean {
      return id === latestIssuedId;
    },
  };
}
