import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import axios from "@/platform/utils/axios";

import { usePeople, usePeopleMutations } from "../people";

vi.mock("@/platform/utils/axios", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

const mockedGet = vi.mocked(axios.get);
const mockedPost = vi.mocked(axios.post);

const C = "5b6a1f2e-2c4b-4a9d-9a1e-2f0c7d8e4b31";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("usePeople", () => {
  beforeEach(() => vi.clearAllMocks());

  /** The route never lists the owner, so a card built from it alone is wrong. */
  it("puts the owner in front of the invited addresses", async () => {
    mockedGet.mockResolvedValue({
      data: {
        invited_users: [
          {
            email: "sam@example.com",
            role: "viewer",
            invited_at: "2026-08-20",
          },
        ],
      },
    });

    const { result } = renderHook(() => usePeople(C, "owner@example.com"), {
      wrapper,
    });
    await waitFor(() => expect(result.current.data).toBeDefined());

    expect(result.current.data).toEqual([
      { email: "owner@example.com", role: "owner", since: "" },
      { email: "sam@example.com", role: "viewer", since: "2026-08-20" },
    ]);
  });
});

/**
 * Inviting, and the part of it that is easy to get wrong: the route answers 2xx
 * even when nobody was invited, so the failure lists in the body are the only
 * signal. `failures[].reason` is now populated by the service, and it is what a
 * reader can act on — an address that already has access reads very differently
 * from one that does not exist.
 */
describe("usePeopleMutations — invite", () => {
  beforeEach(() => vi.clearAllMocks());

  const inviteWith = async (data: unknown) => {
    mockedPost.mockResolvedValue({ data });
    const { result } = renderHook(() => usePeopleMutations(C), { wrapper });
    return result.current.invite.mutateAsync({
      email: "sam@example.com",
      role: "viewer",
    });
  };

  it("resolves when the body carries no failures", async () => {
    await expect(inviteWith({ invited: 1 })).resolves.toBeUndefined();
    expect(mockedPost).toHaveBeenCalledWith(
      `/api/knowledge/v1/${C}/invitations`,
      { invitations: [{ email: "sam@example.com", role: "viewer" }] },
    );
  });

  it("reports the service's reason, with the address it belongs to", async () => {
    await expect(
      inviteWith({
        failed_emails: ["sam@example.com"],
        failures: [
          { email: "sam@example.com", reason: "User already has access" },
        ],
      }),
    ).rejects.toThrow("sam@example.com: User already has access");
  });

  /** A reason with no address still says more than the fallback sentence. */
  it("reports a reason that names no address", async () => {
    await expect(
      inviteWith({ failures: [{ reason: "Invitation limit reached" }] }),
    ).rejects.toThrow("Invitation limit reached");
  });

  /**
   * Before the service populated `failures`, a refusal arrived as bare
   * addresses — reading only the reasons would report a grant that never
   * happened.
   */
  it("still fails on bare addresses with no reason", async () => {
    await expect(
      inviteWith({ failed_emails: ["sam@example.com"] }),
    ).rejects.toThrow("sam@example.com was not invited.");
  });
});
