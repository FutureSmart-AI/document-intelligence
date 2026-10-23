"use client";

import { useState } from "react";

import Icon from "@/components/Icon";
import { Btn, EmptyNote, IconBtn, Input, Pill, Select } from "@/components/ui";
import {
  usePeople,
  usePeopleMutations,
  type GrantableRole,
} from "@/data/people";
import { useOwnerId } from "@/data/session";
import { apiMessage } from "@/lib/apiError";
import { useToast } from "@/platform/components/ToastContext";

import { ROLE_NOTE } from "./constants";

/**
 * Who else can reach this collection.
 *
 * Two roles, because the service grants two: an editor can change the
 * collection, a viewer can only ask it questions. The owner is shown first and
 * cannot be changed or removed here — the route that would do it takes an
 * invitation, and the owner has none.
 *
 * There is no pending state and no resend: on this service an invitation *is*
 * the grant, so a listed address already has access.
 */

export default function SharePeople({
  collectionId,
}: {
  collectionId: string;
}) {
  const { toast } = useToast();
  const owner = useOwnerId();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<GrantableRole>("viewer");
  const people = usePeople(collectionId, owner);
  const mutations = usePeopleMutations(collectionId);

  const invite = async () => {
    const address = email.trim();
    if (!address) return;
    try {
      await mutations.invite.mutateAsync({ email: address, role });
      setEmail("");
      toast.success(`${address} can now reach this collection.`);
    } catch (error) {
      // The service validates the address itself — an invalid one is a 422
      // whose `detail` names the field, which is worth showing verbatim.
      toast.error(apiMessage(error, "That invitation could not be sent."));
    }
  };

  const invited = (people.data ?? []).filter(
    (person) => person.role !== "owner",
  );

  return (
    <div className="grid gap-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          type="email"
          aria-label="Email address to invite"
          placeholder="name@company.com"
          className="min-w-[180px] flex-1"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") invite();
          }}
        />
        <Select
          aria-label="Role for the person being invited"
          fit
          value={role}
          onChange={(event) => setRole(event.target.value as GrantableRole)}
        >
          <option value="viewer">Viewer</option>
          <option value="editor">Editor</option>
        </Select>
        <Btn
          variant="primary"
          disabled={!email.trim() || mutations.invite.isPending}
          onClick={invite}
        >
          Invite
        </Btn>
      </div>
      <p className="text-[11.5px] text-ink-3">{ROLE_NOTE[role]}.</p>

      {people.isLoading ? (
        <EmptyNote>Loading who can reach this collection…</EmptyNote>
      ) : (
        <div className="grid gap-1.5">
          {owner && (
            <div className="flex flex-wrap items-center gap-2 rounded-di-md border border-line bg-sunken px-3 py-2.5">
              <span className="grid">
                <span className="text-[13px] font-semibold text-ink">
                  {owner}
                </span>
                <span className="text-[11.5px] text-ink-3">
                  Created this collection
                </span>
              </span>
              <Pill tone="accent" className="ml-auto">
                Owner
              </Pill>
            </div>
          )}

          {invited.map((person) => (
            <div
              key={person.email}
              className="flex flex-wrap items-center gap-2 rounded-di-md border border-line px-3 py-2.5"
            >
              <span className="grid">
                <span className="text-[13px] font-semibold text-ink">
                  {person.email}
                </span>
                <span className="text-[11.5px] text-ink-3">
                  {person.since
                    ? `Invited ${new Date(person.since).toLocaleDateString(
                        "en-GB",
                        { day: "2-digit", month: "short", year: "numeric" },
                      )}`
                    : "Invited"}
                </span>
              </span>
              <div className="ml-auto flex items-center gap-1.5">
                <Select
                  aria-label={`Role for ${person.email}`}
                  fit
                  className="py-1.5"
                  value={person.role}
                  disabled={mutations.setRole.isPending}
                  onChange={async (event) => {
                    const next = event.target.value as GrantableRole;
                    try {
                      await mutations.setRole.mutateAsync({
                        email: person.email,
                        role: next,
                      });
                      toast.success(`${person.email} is now a ${next}.`);
                    } catch (error) {
                      toast.error(
                        apiMessage(error, "That role could not be changed."),
                      );
                    }
                  }}
                >
                  <option value="viewer">Viewer</option>
                  <option value="editor">Editor</option>
                </Select>
                <IconBtn
                  label={`Remove ${person.email}`}
                  disabled={mutations.remove.isPending}
                  onClick={async () => {
                    try {
                      await mutations.remove.mutateAsync(person.email);
                      toast.success(`${person.email} was removed.`);
                    } catch (error) {
                      toast.error(
                        apiMessage(error, "They could not be removed."),
                      );
                    }
                  }}
                >
                  <Icon name="x" className="size-4" />
                </IconBtn>
              </div>
            </div>
          ))}

          {!invited.length && (
            <EmptyNote>
              Nobody else has been invited. An invitation gives one person
              access to this collection and nothing else in the account.
            </EmptyNote>
          )}
        </div>
      )}
    </div>
  );
}
