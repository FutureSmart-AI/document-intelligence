"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { APP } from "@/lib/routes";

/**
 * Root route. Every page of this app is served under `/document-intelligence`,
 * so `/` only exists to forward anyone who lands on the bare host.
 */
export default function RootPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace(APP);
  }, [router]);

  return null;
}
