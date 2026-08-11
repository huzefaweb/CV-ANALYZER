import type { NextConfig } from "next";

// AC#3/NFR-12 (Story 2.4): protected pages must never be servable from the
// browser's back-forward cache after logout or expiry. `no-store` is what
// makes Chrome/Firefox refuse to bfcache the response. Set at the Next.js
// config layer, not only in middleware — Next's own dynamic-rendering
// response pipeline otherwise overwrites a middleware-set Cache-Control
// header for `force-dynamic` pages.
// `/login` and `/session-expired` are deliberately excluded — neither ever
// renders protected content, so there is nothing on them for bfcache to
// expose (Next's own production default for dynamic pages already includes
// `no-store` there anyway).
const nextConfig: NextConfig = {
  async headers() {
    return [
      {
        source: "/workspace",
        headers: [{ key: "Cache-Control", value: "no-store" }],
      },
      {
        source: "/workspace/:path*",
        headers: [{ key: "Cache-Control", value: "no-store" }],
      },
      {
        source: "/new-analysis",
        headers: [{ key: "Cache-Control", value: "no-store" }],
      },
    ];
  },
};

export default nextConfig;
