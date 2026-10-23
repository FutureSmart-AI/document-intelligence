import { expiresAtFromRefresh } from "@/platform/components/session/session-countdown";
import { authServiceUrl } from "@/platform/config";
import axios from "@/platform/utils/axios";

import { Profile } from "./auth-types";
import { PROFILE_CACHE_KEY, SESSION_ERROR } from "./constants";

/** Read cached profile from sessionStorage (returns null if absent/invalid). */
export function getCachedProfile(): Profile | null {
  try {
    const raw = sessionStorage.getItem(PROFILE_CACHE_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as Profile;
  } catch {
    return null;
  }
}

/** Write profile to sessionStorage for stale-while-revalidate on next visit. */
export function setCachedProfile(profile: Profile) {
  try {
    sessionStorage.setItem(PROFILE_CACHE_KEY, JSON.stringify(profile));
  } catch {
    // Storage full or unavailable — non-critical
  }
}

/** Clear cached profile (used on logout or session expiry). */
export function clearCachedProfile() {
  try {
    sessionStorage.removeItem(PROFILE_CACHE_KEY);
  } catch {
    // non-critical
  }
}

export const fetchProfile = async (): Promise<Profile> => {
  const profileUrl =
    process.env.NEXT_PUBLIC_AUTH_MODE === "standalone"
      ? "/api/knowledge/auth/profile"
      : `${authServiceUrl}/user/me`;
  const response = await axios.get(profileUrl, {
    withCredentials: true,
  });
  if (response.status === 200) {
    return response.data;
  }
  throw new Error(SESSION_ERROR);
};

/**
 * Renew the auth session. POSTs to the auth service's `/refresh` endpoint, which
 * does a Cognito `grant_type=refresh_token` exchange and issues a fresh
 * `access_token` + `id_token` via Set-Cookie (the refresh token itself is not
 * rotated).
 *
 * Sends **no request body at all** — the endpoint only falls back to the
 * HTTP-only `refresh_token` cookie when the body fails to parse; an empty `{}`
 * is parsed successfully and 400s with "No refresh token provided".
 *
 * Returns the absolute expiry (epoch ms) computed from the response's
 * `expires_in`, or null when the body carries no usable TTL. Rejects (401) when
 * the refresh token has expired, which the caller treats as an unrecoverable
 * session and hands off to session-expiry.
 */
export const refreshSession = async (): Promise<number | null> => {
  const response = await axios.post(`${authServiceUrl}/refresh`, undefined, {
    withCredentials: true,
  });
  return expiresAtFromRefresh(response.data);
};
