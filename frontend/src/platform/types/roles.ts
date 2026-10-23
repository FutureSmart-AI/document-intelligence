/** The viewer's role on a collection. The knowledge service has no sharing, so
 * `AuthBridge` always reports `"owner"` for a signed-in viewer. */
export type UserRole = "owner" | "editor" | "viewer";
