import type { ReactNode } from "react";
import "./globals.css";
import AppBar from "./AppBar";

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a className="skip-link" href="#main">
          Skip to main content
        </a>
        <AppBar />
        {children}
      </body>
    </html>
  );
}
