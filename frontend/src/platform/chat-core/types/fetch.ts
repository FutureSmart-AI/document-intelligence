/**
 * The part of `fetch` this package actually uses.
 *
 * Deliberately narrower than `typeof fetch`. The global signature also carries
 * statics — React/Next add `preconnect` to it — which a caller injecting a test
 * double has no reason to provide and which none of our call sites touch: every
 * use is `doFetch(url, init)` awaiting a `Response`. Typing the injection point
 * as `typeof fetch` forced `vi.fn()` doubles to be cast at each site, and made
 * typecheck results depend on which `fetch` declaration the toolchain happened
 * to resolve.
 *
 * A real `fetch` still satisfies this, so passing one is unchanged.
 */
export type FetchLike = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;
