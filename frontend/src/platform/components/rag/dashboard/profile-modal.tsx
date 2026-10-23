"use client";

import { memo, useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Image from "next/image";
import { useRouter } from "next/navigation";

import { useQueryClient } from "@tanstack/react-query";
import { map } from "lodash-es";
import { Fingerprint, LogOut, Mail, Sparkles, User, Zap } from "lucide-react";

import { CopyToClipboard } from "@/platform/components/conversations/copy-to-clipboard";
import { useToast } from "@/platform/components/ToastContext";
import { Button } from "@/platform/components/ui";
import { authLogoutUrl } from "@/platform/config";
import { useAuth } from "@/platform/hooks/useAuth";
import { solutionUrlForPath } from "@/platform/lib/host-routing";
import api from "@/platform/utils/axios";

import { clearCachedProfile } from "./auth-apis";

interface ProfileDropdownProps {
  collapsed: boolean;
  inHeader?: boolean;
}

const ProfileDropdown = ({
  collapsed,
  inHeader = false,
}: ProfileDropdownProps) => {
  const router = useRouter();
  const { toast } = useToast();
  const { profile } = useAuth();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const [mounted, setMounted] = useState(false);
  const [panelStyle, setPanelStyle] = useState<React.CSSProperties>({});

  // The NL2SQL product supplies this context from its own session query, so the
  // menu can render one paint before `GET /user/me` answers — destructuring a
  // null profile there took the whole page down with a client-side exception.
  const { picture, name, user_tier, energy, email, user_id } = profile ?? {};

  useEffect(() => {
    setMounted(true);
  }, []);

  const positionPanel = useCallback(() => {
    const trigger = buttonRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const estimatedWidth = panelRef.current?.offsetWidth || 320; // tailwind w-80 ~ 320px
    const estimatedHeight = panelRef.current?.offsetHeight || 240;
    const gap = 8;

    if (inHeader) {
      // Position below the button when in header
      let top = rect.bottom + gap;
      let left = rect.left;

      // Align to right edge of button if panel would overflow
      if (left + estimatedWidth > window.innerWidth - 4) {
        left = Math.max(4, window.innerWidth - estimatedWidth - 4);
      }

      // If panel would overflow bottom, position above button
      if (top + estimatedHeight > window.innerHeight - 4) {
        top = Math.max(4, rect.top - estimatedHeight - gap);
      }

      setPanelStyle({
        position: "fixed",
        top,
        left,
        zIndex: 1000,
      });
    } else {
      // Original side positioning for sidebar
      let top = rect.top;
      let left = rect.right + gap;
      // Clamp within viewport; if overflow right, try placing on left
      if (left + estimatedWidth > window.innerWidth - 4) {
        left = Math.max(4, rect.left - estimatedWidth - gap);
      }
      // Clamp vertical
      if (top + estimatedHeight > window.innerHeight - 4) {
        top = Math.max(4, window.innerHeight - estimatedHeight - 4);
      }
      setPanelStyle({
        position: "fixed",
        top,
        left,
        zIndex: 1000,
      });
    }
  }, [inHeader, buttonRef, panelRef]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onClick = (e: MouseEvent) => {
      const target = e.target as Node;
      const inButton = containerRef.current?.contains(target);
      const inPanel = panelRef.current?.contains(target);
      if (!inButton && !inPanel) {
        setOpen(false);
      }
    };
    const onReposition = () => {
      if (!open) return;
      positionPanel();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("click", onClick);
    window.addEventListener("resize", onReposition);
    window.addEventListener("scroll", onReposition, true);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("click", onClick);
      window.removeEventListener("resize", onReposition);
      window.removeEventListener("scroll", onReposition, true);
    };
  }, [open, positionPanel]);

  useEffect(() => {
    if (open) positionPanel();
  }, [open, positionPanel]);

  if (!profile) return null;

  const clearAllCookies = () => {
    if (typeof document === "undefined" || typeof window === "undefined")
      return;

    const cookies = document.cookie ? document.cookie.split(";") : [];
    const hostnameParts = window.location.hostname.split(".");
    const expiry = new Date(0).toUTCString();

    cookies.forEach((cookie) => {
      const eqPos = cookie.indexOf("=");
      const cookieName =
        eqPos > -1 ? cookie.slice(0, eqPos).trim() : cookie.trim();

      if (!cookieName) return;

      // Clear cookie for current path and root path
      document.cookie = `${cookieName}=;expires=${expiry};path=/`;

      // Clear cookie across domain levels
      for (let i = 0; i < hostnameParts.length; i += 1) {
        const domain = hostnameParts.slice(i).join(".");

        document.cookie = `${cookieName}=;expires=${expiry};path=/;domain=${domain}`;
        document.cookie = `${cookieName}=;expires=${expiry};path=/;domain=.${domain}`;
      }
    });
  };

  const handleLogout = async () => {
    try {
      if (process.env.NEXT_PUBLIC_AUTH_MODE === "standalone") {
        await api.post("/api/knowledge/auth/logout");
      } else {
        await api.get(authLogoutUrl, { withCredentials: true });
      }

      clearAllCookies();

      // Clear profile cache from sessionStorage
      clearCachedProfile();

      // Clear React Query cache
      queryClient.clear();

      // Clear any service worker caches if they exist
      if ("caches" in window) {
        const cacheNames = await caches.keys();
        await Promise.all(
          map(cacheNames, (cacheName) => caches.delete(cacheName)),
        );
      }

      toast.success("You have been logged out.");

      // This menu is mounted by all three shells, so where "out" leads depends
      // on which one is on screen. Inside a product the agent UI's home is not
      // it: that route belongs to the other host and only bounces a signed-out
      // viewer back to login, so leave for the product's public page instead.
      // A full navigation, not router.push — it is a different origin.
      if (process.env.NEXT_PUBLIC_AUTH_MODE === "standalone") {
        window.location.assign("/document-intelligence/");
        return;
      }
      const solution = solutionUrlForPath(window.location.pathname);
      if (solution) {
        window.location.assign(solution);
        return;
      }

      router.push("/");
    } catch (error) {
      toast.error(
        (error as any)?.response?.data?.detail ||
          error.message ||
          "Logout failed",
      );
    }
  };

  return (
    <div ref={containerRef} className="relative">
      <Button
        variant="ghost"
        size="sm"
        fullWidth
        className={`truncate text-sm font-semibold text-slate-900 ${collapsed ? "justify-center" : "justify-start"}`}
        startIcon={
          picture ? (
            <Image
              src={picture as string}
              alt={name}
              width={28}
              height={28}
              className="size-7 rounded-full object-cover"
            />
          ) : (
            <User className="size-4" aria-hidden="true" />
          )
        }
        onClick={() => setOpen((v) => !v)}
        ref={
          buttonRef as React.RefObject<HTMLButtonElement & HTMLAnchorElement>
        }
      >
        {collapsed ? null : name}
      </Button>

      {mounted && open
        ? createPortal(
            <div
              role="menu"
              ref={panelRef}
              style={panelStyle}
              className="w-80 rounded-2xl bg-white p-3 shadow-2xl backdrop-blur-xl cursor-pointer border border-white/20"
            >
              <div className="absolute inset-x-3 -top-px h-px bg-linear-to-r from-indigo-500 via-sky-500 to-violet-500 rounded-full" />
              <div className="px-2 py-2">
                <div className="flex items-center gap-3 rounded-xl bg-white/60 p-2">
                  <span className="relative inline-flex p-px rounded-full bg-linear-to-r from-indigo-500 via-sky-500 to-violet-500">
                    <span className="rounded-full bg-white p-px">
                      {picture ? (
                        <Image
                          src={picture as string}
                          alt={name}
                          width={32}
                          height={32}
                          className="size-8 rounded-full object-cover"
                        />
                      ) : (
                        <span className="inline-flex size-8 items-center justify-center rounded-full bg-slate-100 text-slate-700 ring-1 ring-slate-200">
                          <User className="size-4" aria-hidden="true" />
                        </span>
                      )}
                    </span>
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-semibold text-slate-900">
                        {name}
                      </span>
                    </div>
                    <div className="mt-1 flex items-center gap-2 text-xs text-slate-600">
                      <Mail className="size-3.5 text-slate-400" />
                      <CopyToClipboard
                        name={email}
                        className="truncate text-xs text-slate-500 hover:text-slate-700"
                        position={inHeader ? "bottom-right" : undefined}
                      >
                        {email}
                      </CopyToClipboard>
                    </div>
                  </div>
                </div>

                <div className="mt-2 flex flex-wrap items-center gap-2 px-0.5">
                  {user_tier ? (
                    <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700 ring-1 ring-slate-200">
                      <Sparkles className="size-3.5 text-indigo-500" />
                      {user_tier}
                    </span>
                  ) : null}
                  {energy ? (
                    <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-xs text-amber-700 ring-1 ring-amber-200">
                      <Zap className="size-3.5 text-amber-500" />
                      Energy: {energy}
                    </span>
                  ) : null}
                  <span className="inline-flex items-center gap-1 rounded-full bg-slate-50 px-2 py-0.5 text-xs text-slate-600 ring-1 ring-slate-200">
                    <Fingerprint className="size-3.5 text-slate-400" />
                    <CopyToClipboard
                      name={user_id}
                      className="text-xs text-slate-500 hover:text-slate-700"
                      position={inHeader ? "bottom-right" : undefined}
                    >
                      {user_id}
                    </CopyToClipboard>
                  </span>
                </div>
              </div>

              <div className="my-2 h-px bg-slate-200/70" />

              <Button
                variant="danger"
                size="sm"
                fullWidth
                onClick={handleLogout}
                loadingText="Logging out..."
                startIcon={<LogOut className="size-4" aria-hidden="true" />}
              >
                Logout
              </Button>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
};

export default memo(ProfileDropdown);
