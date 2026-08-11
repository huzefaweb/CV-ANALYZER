"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

export default function LoginForm({ returnTo }: { returnTo?: string }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);

    let response: Response;
    try {
      response = await fetch("/api/identity/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
    } catch {
      // Offline/unreachable gateway: neutral reconnect guidance, not a
      // stuck spinner (EXPERIENCE.md Login state pattern).
      setSubmitting(false);
      setError("Unable to reach the server. Check your connection and try again.");
      return;
    }

    setSubmitting(false);
    if (!response.ok) {
      // Neutral message (AC#3): never confirms which detail was wrong, and
      // is identical on every retry — no lockout/backoff state is kept.
      setError("Sign in failed. Check your email and password and try again.");
      return;
    }
    // AC#2 (Story 2.4): returnTo is already validated server-side by
    // login/page.tsx via isAllowedReturnPath — this component trusts it as
    // a prop, never a raw query string.
    router.push(returnTo ?? "/");
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <div>
        <label htmlFor="email">Email</label>
        <input
          id="email"
          name="email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
      </div>
      <div>
        <label htmlFor="password">Password</label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
      </div>
      {error ? (
        <p role="alert" aria-live="assertive">
          {error}
        </p>
      ) : null}
      <button type="submit" disabled={submitting}>
        {submitting ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
