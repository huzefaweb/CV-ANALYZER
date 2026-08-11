// AR-5-8/UX-DR9: the single validation point for a `return_to` relative
// path. It is attacker-controlled at every hop it passes through
// (session-expired's query string, then login's), so both callers
// revalidate independently — neither trusts the other's encoding.
const ALLOWED_RETURN_PREFIXES = ["/workspace", "/new-analysis"];

export function isAllowedReturnPath(path: unknown): path is string {
  // Next.js parses a repeated query key (`?return_to=a&return_to=b`) into a
  // string[], not a string — reject any non-string input outright rather
  // than trusting the `{ return_to?: string }` searchParams type, which
  // does not hold at runtime for a duplicated key.
  if (typeof path !== "string" || !path) return false;
  if (!path.startsWith("/") || path.startsWith("//")) return false;
  if (path.includes("\\") || path.includes("..") || path.includes("?") || path.includes("#")) return false;
  return ALLOWED_RETURN_PREFIXES.some((prefix) => path === prefix || path.startsWith(`${prefix}/`));
}
