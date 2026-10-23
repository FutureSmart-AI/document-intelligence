"use client";

import { type ReactNode } from "react";

import { useQueryClient } from "@tanstack/react-query";

import type { Profile as AgentProfile } from "@/platform/components/rag/dashboard/auth-types";
import { AuthContext } from "@/platform/components/rag/dashboard/AuthContext";

import { useCollection } from "./collections";
import { useSession } from "./session";

/**
 * Lets this product mount the agent UI's own components.
 *
 * Everything reused from `src/components` — the documents list and its detail
 * panel, the data table, Explore, the chat stack, the extracted fields page —
 * reads its knowledge base and viewer from the agent UI's `AuthContext`. Rather
 * than fork those components, this supplies that context from what this app
 * already has: the session from `./session` and the collection in `?collection=`.
 *
 * A collection is a knowledge base and nothing else, so `agentId` and
 * `knowledgeId` are the same id here — the components that take one take the
 * knowledge base.
 *
 * It deliberately does **not** use the agent UI's `AuthProvider`, which owns
 * login redirects, session-expiry countdowns and the `?agent_id=` parameter —
 * this product handles its own session (see `RequireSession`), and mounting both
 * would give two components the same job.
 */
export default function AuthBridge({
  collectionId,
  children,
}: {
  /** The collection this subtree is scoped to, from `?collection=`. */
  collectionId?: string;
  children: ReactNode;
}) {
  const { profile } = useSession();
  const client = useQueryClient();
  const { collection } = useCollection(collectionId);

  return (
    <AuthContext.Provider
      value={{
        // The agent UI's `Profile` carries session bookkeeping this product does
        // not use; the fields its components read are the identity ones.
        profile: (profile as unknown as AgentProfile) ?? null,
        // There is no agent record behind a collection, and none of the mounted
        // components read one — they take the knowledge id below.
        selectedAgent: null,
        agentId: collectionId ?? null,
        knowledgeId: collection?.knowledgeId ?? null,
        isKnowledgeEnabled: Boolean(collection?.knowledgeEnabled),
        refetchSelectedAgent: () => {
          client.invalidateQueries({
            queryKey: ["di-collection", collectionId ?? null],
          });
          client.invalidateQueries({ queryKey: ["di-collections"] });
        },
        // A collection is a documents product; the NL2SQL service is never part
        // of one, so the views that branch on it take the "not present" path.
        isNl2sqlEnabled: false,
        nl2sqlId: null,
        actionsServiceIds: [],
        integrationServiceIds: [],
        /**
         * The knowledge service has no sharing: there are no invitations and no
         * roles on it, and a knowledge base another account owns answers 403 on
         * every route. So whoever can read a collection owns it, and the role
         * lookup this used to make against the chat service is gone.
         */
        userRole: profile ? "owner" : null,
        isOwner: Boolean(profile),
        isEditor: false,
        isViewer: false,
        // Session expiry is owned by this product's own session handling.
        sessionExpiresAt: null,
        stayLoggedIn: () => Promise.resolve(false),
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}
