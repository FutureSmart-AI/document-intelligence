"use client";

import { createContext, useContext, type ReactNode } from "react";

import { useQuery } from "@tanstack/react-query";

import { fetchProfile } from "@/platform/components/rag/dashboard/auth-apis";
import { authLoginUrlFor } from "@/platform/config";

/** The signed-in user, as returned by the auth service's `GET /user/me`. */
export interface Profile {
  email: string;
  name?: string;
  /** The account's own id — what the knowledge service stores on a session. */
  user_id?: string;
  [key: string]: unknown;
}

interface SessionValue {
  profile: Profile | null;
  isLoading: boolean;
  /** True once the profile request failed — the session is gone. */
  isSignedOut: boolean;
}

const SessionContext = createContext<SessionValue>({
  profile: null,
  isLoading: true,
  isSignedOut: false,
});

/**
 * The signed-in user for the whole app.
 *
 * Every request this product makes is scoped by `owner_id`, which is this
 * profile's email — so there is no data at all until it resolves. The token
 * itself lives in an httpOnly cookie set by the auth service; the browser
 * attaches it, and a 401 anywhere means the session is over.
 */
export function SessionProvider({ children }: { children: ReactNode }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["profile"],
    queryFn: fetchProfile,
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: 5 * 60 * 1000,
  });

  return (
    <SessionContext.Provider
      value={{
        profile: (data as Profile) ?? null,
        isLoading,
        isSignedOut: isError,
      }}
    >
      {children}
    </SessionContext.Provider>
  );
}

export function useSession(): SessionValue {
  return useContext(SessionContext);
}

/**
 * The signed-in user's email, which every API call uses as `owner_id`. Returns
 * `undefined` while the profile is still loading, so queries stay disabled
 * rather than firing unscoped.
 */
export function useOwnerId(): string | undefined {
  return useSession().profile?.email;
}

/**
 * The signed-in account's id.
 *
 * Deliberately not the email: a session's `user_id` is the account id, so this
 * is what the sessions route matches on when the list is narrowed to "mine".
 */
export function useUserId(): string | undefined {
  return useSession().profile?.user_id;
}

/** Sends the browser to SSO, returning here afterwards. */
export function signIn(): void {
  window.location.assign(authLoginUrlFor(window.location.href));
}
