/**
 * The hosted login (auth service). Called directly, cross-origin, from the
 * browser: its session cookie is host-only on `auth.futuresmart.ai`
 * (`samesite=none; secure`, no `Domain`), and it reflects the caller's exact
 * origin with `access-control-allow-credentials: true`. Never route it through
 * this app's own origin — the browser would attach nothing and every call
 * would 401. Override with `NEXT_PUBLIC_AUTH_URL` for another environment.
 */
const AUTH_SERVICE_URL = "https://auth.futuresmart.ai";

/**
 * Empty in every build: Knowledge calls go out relative to the host serving
 * the page (`/api/knowledge/*`), and that host routes them — CloudFront in a
 * deployment, the dev server's rewrite locally (see `next.config.ts`). There is
 * deliberately no browser-side API origin to configure.
 */
export const baseUrl = "";

export const authServiceUrl =
  process.env.NEXT_PUBLIC_AUTH_URL || AUTH_SERVICE_URL;

// Derive login/logout URLs from authServiceUrl so every environment gets the
// correct path appended.
export const authLoginUrl = `${authServiceUrl}/agent/login`;

/**
 * Build an SSO login URL that returns the browser to a route in this app.
 *
 * Authentication happens on a different origin, so this must be an absolute
 * URL and must be passed to the auth service as `redirect` — that is the key
 * `/agent/login` reads, and it drops unknown params silently, falling back to
 * the Referer (which Chrome trims to the bare origin). Keeping this in one
 * place prevents entry points from silently dropping the destination.
 */
export function authLoginUrlFor(returnTo: string): string {
  return `${authLoginUrl}?${new URLSearchParams({
    redirect: returnTo,
  }).toString()}`;
}

export const authLogoutUrl = `${authServiceUrl}/logout`;

// Speech-to-text gateway. Hard-coded to the fs-voice gateway; the browser calls
// it directly and chat-core attaches the (public) gateway key — see STT_API_KEY.
export const sttUrl = "https://stt.futuresmart.ai/api/stt";

/** True when a voice gateway key was supplied at build time (see `.env.example`). */
export const voiceGatewayConfigured = Boolean(
  process.env.NEXT_PUBLIC_VOICE_GATEWAY_KEY,
);

// The chat speech-to-text mic. Off when no gateway key was configured.
export const sttMicEnabled = voiceGatewayConfigured;

// Text-to-speech read-aloud. Same fs-voice gateway as STT; the browser calls it
// directly and chat-core attaches the (public) gateway key — see STT_API_KEY.
export const ttsUrl = sttUrl;

// The read-aloud button (mirrors sttMicEnabled).
export const ttsEnabled = voiceGatewayConfigured;
