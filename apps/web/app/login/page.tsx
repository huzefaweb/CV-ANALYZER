import { authProviderConfigured } from "@/lib/adapter";
import { isAllowedReturnPath } from "@/lib/session";
import LoginForm from "./LoginForm";

// Reads process.env directly at render time — must not be statically
// prerendered at build time, or a runtime-only AUTH0_* config (set via
// docker-compose, absent at `docker build`) would never take effect.
export const dynamic = "force-dynamic";

// UX-DR5/UX-DR8: the public Login/provider-handoff surface. Copy mirrors
// _bmad-output/planning-artifacts/ux-designs/ux-CV-ANALYZER-2026-08-09/mockups/login.html.
export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ return_to?: string | string[] }>;
}) {
  const providerConfigured = authProviderConfigured();
  const { return_to } = await searchParams;
  const returnTo = isAllowedReturnPath(return_to) ? return_to : undefined;

  return (
    <main id="main">
      <div className="stage">
        <section className="intro" aria-labelledby="title">
          <div className="eyebrow">Private recruiter workspace</div>
          <h1 id="title">Compare candidates against consistent job criteria with traceable Evidence.</h1>
          <p className="lead">CV Analyzer supports recruiter judgment; it does not make hiring decisions.</p>

          <div className="principles">
            <div className="principle">
              <b>One frozen comparison context</b>
              <span>Each Analysis Session compares 1–20 Resumes against one frozen Job Description.</span>
            </div>
            <div className="principle">
              <b>Evidence first</b>
              <span>Inspect favorable and adverse conclusions in their Resume context.</span>
            </div>
            <div className="principle">
              <b>Human verification required</b>
              <span>
                Scores summarize Resume Evidence. They are not suitability, rejection, or hiring recommendations.
              </span>
            </div>
            <div className="principle">
              <b>Private demonstration</b>
              <span>Use synthetic Resume data by default. Access is limited to admitted Recruiters.</span>
            </div>
          </div>
        </section>

        <aside className="handoff" aria-labelledby="access-title">
          <div className="panel">
            <h2 id="access-title">Access CV Analyzer</h2>

            {/* UX-DR8/AC#1: never render both the local form and provider-owned
                authentication — exactly one branch renders. AUTH0_* is unset in
                every environment this story runs against (see story Dev Notes),
                so this else branch is structural, not yet reachable. */}
            {providerConfigured ? (
              <p>Continue through your organization&apos;s configured identity provider.</p>
            ) : (
              <p>
                Sign in with your email and password, or continue through the configured identity provider once
                available. When Auth0 is not configured, CV Analyzer stores only a securely hashed password — never
                your password itself.
              </p>
            )}

            <div className="notice">
              <b>Admission required</b>
              <br />
              Account creation establishes your identity. Workspace access requires Demo Operator approval.
            </div>

            <div className="data">
              <b>Resume data boundary</b>
              <p className="micro">
                Synthetic Resume data is the default. Real Resume use is prohibited unless an external Data Steward
                verifies written purpose and provider consent, and approves the provider&apos;s retention, training,
                and regional-processing terms.
              </p>
            </div>

            {providerConfigured ? null : <LoginForm returnTo={returnTo} />}

            <p className="micro">
              No account? <a href="/register">Create one</a>.
            </p>
          </div>
        </aside>
      </div>
    </main>
  );
}
