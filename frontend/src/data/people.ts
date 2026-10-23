"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { buildApiUrl } from "@/platform/utils";
import axios from "@/platform/utils/axios";

import { APIS } from "./api";

/**
 * Who else can reach one collection.
 *
 * `GET …/invitations` answers `{ invited_users: [{ email, role, invited_at }] }`
 * and does **not** include the owner, so the owner is prepended here from the
 * signed-in profile — the People card would otherwise show a collection with
 * nobody on it, which is never true.
 *
 * There is no "pending" state on this service: an invitation is the grant. So
 * nothing here reports one, and there is no resend.
 */

export type Role = "owner" | "editor" | "viewer";
/** Every role that can be granted — `owner` is not one of them. */
export type GrantableRole = Exclude<Role, "owner">;

export interface Person {
  email: string;
  role: Role;
  /** When they were invited; the owner has no such date. */
  since: string;
}

export function usePeople(
  collectionId: string | undefined,
  owner: string | undefined,
) {
  return useQuery({
    queryKey: ["people", collectionId],
    enabled: Boolean(collectionId),
    queryFn: async (): Promise<Person[]> => {
      const response = await axios.get<{
        invited_users?: Array<{
          email: string;
          role: GrantableRole;
          invited_at?: string | null;
        }>;
      }>(
        buildApiUrl(APIS.INVITATIONS, {
          knowledge_id: collectionId as string,
        }),
      );
      return [
        ...(owner ? [{ email: owner, role: "owner" as const, since: "" }] : []),
        ...(response.data.invited_users ?? []).map((person) => ({
          email: person.email,
          role: person.role,
          since: person.invited_at ?? "",
        })),
      ];
    },
  });
}

export function usePeopleMutations(collectionId: string | undefined) {
  const client = useQueryClient();
  const invalidate = () =>
    client.invalidateQueries({ queryKey: ["people", collectionId] });

  const invite = useMutation({
    mutationFn: async (input: { email: string; role: GrantableRole }) => {
      const response = await axios.post<{
        failed_emails?: string[];
        failures?: Array<{ email?: string; reason?: string }>;
      }>(
        buildApiUrl(APIS.INVITATIONS, {
          knowledge_id: collectionId as string,
        }),
        { invitations: [input] },
      );
      // The route answers 2xx even when every address failed, so the two
      // failure lists are what decides success. `failures` carries the reason
      // and `failed_emails` the addresses; either alone means this did not
      // work, and reading only one would report a grant that never happened.
      const { failed_emails = [], failures = [] } = response.data ?? {};
      if (!failed_emails.length && !failures.length) return;
      // The reason is the whole point of the failure list, and it says nothing
      // about which address it belongs to — so the address is put in front of
      // it here rather than left to the reader to guess.
      const reasons = failures
        .map((failure) =>
          failure.reason
            ? failure.email
              ? `${failure.email}: ${failure.reason}`
              : failure.reason
            : undefined,
        )
        .filter(Boolean)
        .join("; ");
      throw new Error(
        reasons ||
          `${failed_emails.join(", ") || input.email} was not invited.`,
      );
    },
    onSuccess: invalidate,
  });

  const setRole = useMutation({
    mutationFn: async (input: { email: string; role: GrantableRole }) => {
      await axios.patch(
        buildApiUrl(APIS.INVITATION, {
          knowledge_id: collectionId as string,
          user_email: encodeURIComponent(input.email),
        }),
        { role: input.role },
      );
    },
    onSuccess: invalidate,
  });

  const remove = useMutation({
    mutationFn: async (email: string) => {
      await axios.delete(
        buildApiUrl(APIS.INVITATION, {
          knowledge_id: collectionId as string,
          user_email: encodeURIComponent(email),
        }),
      );
    },
    onSuccess: invalidate,
  });

  return { invite, setRole, remove };
}
