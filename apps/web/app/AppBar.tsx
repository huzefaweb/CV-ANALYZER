import { cookies } from "next/headers";
import { SESSION_COOKIE } from "@/lib/gateway";
import NavLinks from "./NavLinks";
import SignOutButton from "./SignOutButton";

// DESIGN.md app-shell: 52px appbar, quiet protected-workspace identity —
// brand-only on public pages (no session cookie yet), brand + nav + sign
// out once signed in. One shared component so every page gets it for free
// from the root layout, rather than each page wiring its own copy.
export default async function AppBar() {
  const cookieStore = await cookies();
  const signedIn = Boolean(cookieStore.get(SESSION_COOKIE)?.value);

  return (
    <header className="bar no-print">
      <span className="brand">CV Analyzer</span>
      {signedIn ? (
        <nav aria-label="Primary">
          <NavLinks />
          <span className="identity">Signed in</span>
          <SignOutButton />
        </nav>
      ) : null}
    </header>
  );
}
