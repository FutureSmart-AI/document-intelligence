"use client";

import { useEffect, useState } from "react";

import { THEME_STORAGE_KEY } from "@/lib/constants";

import Icon from "./Icon";
import { IconBtn } from "./ui";

type Theme = "light" | "dark";

/** The remembered choice, or none when the viewer has never picked one. */
function storedTheme(): Theme | null {
  try {
    const value = window.localStorage.getItem(THEME_STORAGE_KEY);
    return value === "dark" || value === "light" ? value : null;
  } catch {
    return null;
  }
}

function currentTheme(): Theme {
  if (typeof document === "undefined") return "light";
  const stored = storedTheme();
  if (stored) return stored;
  const stamped = document.documentElement.getAttribute("data-theme");
  if (stamped === "dark" || stamped === "light") return stamped;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

/**
 * Light/dark switch for the product's own surfaces.
 *
 * It stamps `data-theme` on `<html>`, which is what the token blocks in
 * `theme.css` key off, and remembers the choice.
 *
 * Two mounts, two starting points. The standalone app applies the stored choice
 * from a script in its layout, before first paint. Inside the agent UI there is
 * no such script — that layout is shared with every other page of that
 * application — so this component applies it on mount instead, which is why the
 * stored value is read here rather than only the stamped attribute.
 *
 * Scope worth knowing: the shared feature views this product mounts from the
 * agent UI (the documents table, Data, Explore, the chat) are painted in that
 * app's literal light palette and do not follow this toggle. Dark applies to the
 * chrome, the public page, Home and this product's own panels.
 */
export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("light");

  // Neither the stored choice nor the system preference exists when this is
  // prerendered, so the first client render has to agree with the server's markup
  // and correct itself here.
  //
  // `color-scheme` is what makes the browser paint form controls, scrollbars and
  // the canvas dark, and it lives on `<html>` — shared with the agent UI, whose
  // screens are light-only. So it is applied while this product is on screen and
  // cleared when it unmounts; `data-theme` may stay, since only this product's
  // tokens read it.
  useEffect(() => {
    const resolved = currentTheme();
    setTheme(resolved);
    const stored = storedTheme();
    if (stored) {
      document.documentElement.setAttribute("data-theme", stored);
      document.documentElement.style.colorScheme = stored;
    }
    return () => {
      document.documentElement.style.colorScheme = "";
    };
  }, []);

  const apply = (next: Theme) => {
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    document.documentElement.style.colorScheme = next;
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Storage disabled — the choice holds for this page only.
    }
  };

  return (
    <IconBtn
      label={
        theme === "dark" ? "Switch to light theme" : "Switch to dark theme"
      }
      onClick={() => apply(theme === "dark" ? "light" : "dark")}
    >
      <Icon name={theme === "dark" ? "moon" : "sun"} className="size-[15px]" />
    </IconBtn>
  );
}
