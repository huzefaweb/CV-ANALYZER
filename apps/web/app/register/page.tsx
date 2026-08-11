import { authProviderConfigured } from "@/lib/adapter";
import RegisterForm from "./RegisterForm";

// See app/login/page.tsx — same process.env-at-render-time reason.
export const dynamic = "force-dynamic";

export default function RegisterPage() {
  const providerConfigured = authProviderConfigured();

  return (
    <main id="main">
      <div className="panel">
        <h1>Create account</h1>
        <div className="notice">
          Account creation establishes your identity. Workspace access requires Demo Operator approval.
        </div>

        {/* AC#1: the active adapter owns registration — never show the local
            form alongside provider-owned registration. */}
        {providerConfigured ? (
          <p>Create your account through your organization&apos;s configured identity provider.</p>
        ) : (
          <RegisterForm />
        )}
        <p>
          Already have an account? <a href="/login">Sign in</a>.
        </p>
      </div>
    </main>
  );
}
