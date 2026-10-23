/**
 * The message shown when a run breaks in a way the user can do nothing with.
 */
export const GENERIC_STREAM_FAILURE =
  "Something went wrong while generating the response. Please try again.";

/**
 * Failures the backend reports as `{"error": …}` carry whatever the server
 * raised. Some of it is written for a human ("You have reached your monthly
 * query limit"), but a run that breaks inside a step reports the raw Python
 * exception instead — `'NoneType' object has no attribute 'get'` is what chart
 * generation raises today, arriving after the answer has already streamed.
 *
 * Those strings tell a user nothing and expose the backend's internals, so they
 * are matched here and replaced.
 */
const INTERNAL_FAILURE_PATTERNS: RegExp[] = [
  /NoneType/,
  /object has no attribute/i,
  /object is not (?:subscriptable|iterable|callable)/i,
  /Traceback \(most recent call last\)/i,
  /\b(?:Attribute|Type|Value|Key|Index|Name|Runtime|Import|Assertion|ZeroDivision)Error\b/,
  /list index out of range/i,
  /unhashable type/i,
  /takes \d+ positional arguments?/i,
  /object at 0x[0-9a-f]+/i,
];

/**
 * Turns a reported stream failure into something worth showing a user: the
 * backend's own message when it was written for one, and a plain fallback when
 * it was an internal exception (or empty).
 */
export function describeStreamFailure(raw?: string | null): string {
  const message = typeof raw === "string" ? raw.trim() : "";
  if (!message) return GENERIC_STREAM_FAILURE;
  return INTERNAL_FAILURE_PATTERNS.some((pattern) => pattern.test(message))
    ? GENERIC_STREAM_FAILURE
    : message;
}
