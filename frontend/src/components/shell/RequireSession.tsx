"use client";

import { useEffect, useRef, type ReactNode } from "react";

import { signIn, useSession } from "@/data/session";
import Loading from "@/platform/components/loading";

import StandaloneLogin from "./StandaloneLogin";

/**
 * Protects the Document Intelligence workspace in both mounts.
 *
 * The product is statically exported, so this client-side profile check is the
 * first point at which its auth cookie can be verified. It renders the agent
 * UI's own loading skeleton, so arriving here from that dashboard shows one
 * continuous loading state rather than two different ones.
 */
export default function RequireSession({ children }: { children: ReactNode }) {
  const { profile, isLoading, isSignedOut } = useSession();
  const redirectStarted = useRef(false);

  useEffect(() => {
    if (
      process.env.NEXT_PUBLIC_AUTH_MODE === "standalone" ||
      !isSignedOut ||
      redirectStarted.current
    )
      return;
    redirectStarted.current = true;
    signIn();
  }, [isSignedOut]);

  if (
    process.env.NEXT_PUBLIC_AUTH_MODE === "standalone" &&
    !isLoading &&
    !profile
  )
    return <StandaloneLogin />;

  if (isLoading || !profile) {
    return <Loading message="Signing you in…" />;
  }

  return <>{children}</>;
}
