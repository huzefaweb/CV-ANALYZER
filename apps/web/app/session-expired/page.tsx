import { isAllowedReturnPath } from "@/lib/session";

export const dynamic = "force-dynamic";

// EXPERIENCE.md:35,178,201: neutral "Session expired" surface. No protected
// content is fetched or rendered here — the protected page's own load
// already redirected here before rendering anything (AC#1).
export default async function SessionExpiredPage({
  searchParams,
}: {
  searchParams: Promise<{ return_to?: string | string[] }>;
}) {
  const { return_to } = await searchParams;
  const signInHref = isAllowedReturnPath(return_to) ? `/login?return_to=${encodeURIComponent(return_to)}` : "/login";

  return (
    <main id="main">
      <h1>Session expired</h1>
      <p>Your session has ended. Sign in again to continue.</p>
      <p>Unsubmitted work from before your session expired may not be recoverable.</p>
      <p>
        <a href={signInHref}>Sign in again</a>
      </p>
    </main>
  );
}
