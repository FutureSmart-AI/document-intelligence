import { afterEach, describe, expect, it, vi } from "vitest";

import { platformCredentialsLabel, productTagline } from "../platformCopy";

describe("platform copy", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("names FutureSmart credentials only in the hosted build", () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_MODE", "hosted");
    expect(platformCredentialsLabel()).toBe("FutureSmart platform credentials");
    expect(productTagline()).toContain("our team");
  });

  it("describes the operator's own keys in a standalone installation", () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_MODE", "standalone");
    expect(platformCredentialsLabel()).toBe(
      "this installation's configured keys",
    );
    expect(productTagline()).not.toContain("our team");
  });
});
