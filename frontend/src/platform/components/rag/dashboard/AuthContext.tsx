"use client";

import { createContext } from "react";

import { noop } from "lodash-es";

import type { IAuthContext } from "./auth-types";

/**
 * The context the shared knowledge views read their collection and viewer from.
 *
 * This product never mounts the agent UI's `AuthProvider` (which fetched an
 * agent from the chat service, polled invitations and ran the dashboard's
 * session countdown). `@/data/AuthBridge` supplies the value instead, from this
 * app's own session and the `?collection=` in the URL.
 */
export const AuthContext = createContext<IAuthContext>({
  profile: null,
  selectedAgent: null,
  agentId: null,
  knowledgeId: null,
  isKnowledgeEnabled: false,
  refetchSelectedAgent: noop,
  isNl2sqlEnabled: false,
  nl2sqlId: null,
  actionsServiceIds: [],
  integrationServiceIds: [],
  userRole: null,
  isOwner: true,
  isEditor: false,
  isViewer: false,
  sessionExpiresAt: null,
  stayLoggedIn: () => Promise.resolve(false),
});
