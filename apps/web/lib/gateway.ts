const GATEWAY_URL = process.env.GATEWAY_URL ?? "http://localhost:8000";

// AD-2/AD-21: this is the only browser-visible session cookie name/shape,
// identical regardless of which identity adapter issued it server-side.
export const SESSION_COOKIE = "session";

export async function gatewayFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${GATEWAY_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
}
