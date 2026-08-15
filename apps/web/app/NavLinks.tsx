"use client";

import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/workspace", label: "Latest work" },
  { href: "/new-analysis", label: "New Analysis" },
];

export default function NavLinks() {
  const pathname = usePathname();
  return (
    <>
      {LINKS.map((link) => (
        <a key={link.href} href={link.href} aria-current={pathname === link.href ? "page" : undefined}>
          {link.label}
        </a>
      ))}
    </>
  );
}
