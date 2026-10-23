import { authLoginUrlFor } from "@/platform/config";

type SessionExpiryHandler = () => void;

// Module-level state: the guard dedups concurrent 401s into one toast + one
// redirect; the handler is supplied by the React bridge once it mounts.
let isExpiring = false;
let registeredHandler: SessionExpiryHandler | null = null;

/** Called by the React bridge on mount to supply the toast/cleanup/redirect handler. */
export function registerSessionExpiryHandler(
  handler: SessionExpiryHandler,
): void {
  registeredHandler = handler;
}

/**
 * Single entry point for "the session has expired". Idempotent — the first call
 * runs the handler (or a fallback redirect); subsequent calls are no-ops until
 * the page reloads or tests reset the state.
 */
export function handleSessionExpiry(): void {
  if (isExpiring) return;
  isExpiring = true;

  if (registeredHandler) {
    registeredHandler();
    return;
  }

  // Fallback: a 401 arrived before the React bridge mounted. Redirect without a
  // toast — degrades to the previous hard-redirect behavior, never worse.
  window.location.href = authLoginUrlFor(window.location.href);
}

/** Test-only: reset module state between tests. */
export function resetSessionExpiryState(): void {
  isExpiring = false;
  registeredHandler = null;
}
