import { useCallback, useEffect, useState } from "react";

const SCREEN_WIDTH = 1024;

export const useSidebarCollapsed = () => {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    if (typeof window !== "undefined") {
      const collapsed = localStorage.getItem("sidebar-collapsed");
      if (collapsed !== null) {
        return collapsed === "true";
      }
      return window.innerWidth < SCREEN_WIDTH;
    }
    return false;
  });

  useEffect(() => {
    let prevWidth = window.innerWidth;

    const handleResize = () => {
      const currWidth = window.innerWidth;
      // Collapse automatically when window shrinks to tablet size (< 1024px)
      if (prevWidth >= SCREEN_WIDTH && currWidth < SCREEN_WIDTH) {
        setSidebarCollapsed(true);
        localStorage.setItem("sidebar-collapsed", "true");
      }
      prevWidth = currWidth;
    };

    window.addEventListener("resize", handleResize);

    // Initial check in case it loaded on small screen
    if (window.innerWidth < SCREEN_WIDTH) {
      setSidebarCollapsed((prev) => {
        if (!prev) {
          localStorage.setItem("sidebar-collapsed", "true");
          return true;
        }
        return prev;
      });
    }

    return () => window.removeEventListener("resize", handleResize);
  }, []);

  const toggleSidebarCollapsed = useCallback(() => {
    setSidebarCollapsed((prev: boolean) => {
      const newCollapsedState = !prev;
      if (typeof window !== "undefined") {
        localStorage.setItem(
          "sidebar-collapsed",
          newCollapsedState ? "true" : "false",
        );
      }
      return newCollapsedState;
    });
  }, []);

  return { sidebarCollapsed, toggleSidebarCollapsed };
};

export default useSidebarCollapsed;
