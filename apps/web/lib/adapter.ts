// AD-21: mirrors apps/gateway/src/adapters/config.py's Settings.auth0_configured
// check exactly — presence of all three selects Auth0, absence (V1 default)
// selects the local adapter. Kept independent per-service (same pattern the
// worker uses for AZURE_OPENAI_*) rather than a network round trip to ask.
export function authProviderConfigured(): boolean {
  return Boolean(
    process.env.AUTH0_DOMAIN && process.env.AUTH0_CLIENT_ID && process.env.AUTH0_CLIENT_SECRET,
  );
}
