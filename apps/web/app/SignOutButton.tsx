"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export default function SignOutButton() {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);

  async function handleSignOut() {
    setSubmitting(true);
    try {
      await fetch("/api/identity/logout", { method: "POST" });
    } catch {
      // Falls through to the redirect regardless — the cookie is cleared
      // server-side on a successful call, and landing on Login is the
      // correct outcome either way (a stale cookie there just re-prompts).
    }
    router.push("/login");
  }

  return (
    <button type="button" className="secondary" onClick={handleSignOut} disabled={submitting}>
      {submitting ? "Signing out…" : "Sign out"}
    </button>
  );
}
