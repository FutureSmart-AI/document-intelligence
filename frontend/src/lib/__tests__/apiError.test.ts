import { describe, expect, it } from "vitest";

import { apiMessage } from "../apiError";

/**
 * The three shapes this service actually answers with, all captured live on
 * 2026-08-23 — plus the one thing that must never reach a reader.
 */

describe("apiMessage", () => {
  it("shows a sentence `detail` verbatim", () => {
    expect(
      apiMessage(
        {
          response: {
            data: { detail: "You can only list your own knowledge services" },
          },
        },
        "fallback",
      ),
    ).toBe("You can only list your own knowledge services");
  });

  it("reads a FastAPI validation list, naming the field", () => {
    // Exactly what `POST …/invitations` answered for `not-an-email`.
    expect(
      apiMessage(
        {
          response: {
            data: {
              detail: [
                {
                  type: "value_error",
                  loc: ["body", "invitations", 0, "email"],
                  msg: "Value error, must be a valid email address",
                },
              ],
            },
          },
        },
        "fallback",
      ),
    ).toBe("email: must be a valid email address");
  });

  it("reads a `message` body", () => {
    expect(
      apiMessage({ response: { data: { message: "Report deleted" } } }, "no"),
    ).toBe("Report deleted");
  });

  it("keeps our own thrown sentence", () => {
    // The invite helper builds one out of the service's per-address failures.
    expect(apiMessage(new Error("bob@x.com was not invited."), "no")).toBe(
      "bob@x.com was not invited.",
    );
  });

  it("never shows axios's status-code sentence", () => {
    // "Request failed with status code 422" tells a reader nothing, so the
    // caller's own wording wins.
    expect(
      apiMessage(
        new Error("Request failed with status code 422"),
        "Try again.",
      ),
    ).toBe("Try again.");
    expect(apiMessage(undefined, "Try again.")).toBe("Try again.");
    expect(apiMessage({ response: { data: {} } }, "Try again.")).toBe(
      "Try again.",
    );
  });
});
