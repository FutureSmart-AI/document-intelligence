import { useEffect, useState } from "react";

const MOBILE_QUERY = "(max-width: 767px)";

/** Tracks the Tailwind `md` breakpoint (768px) so layout can switch between
 * push-based (desktop) and overlay-based (mobile) panels. Returns `false`
 * during SSR/first paint to avoid a hydration mismatch. */
export const useIsMobile = (): boolean => {
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const mediaQuery = window.matchMedia(MOBILE_QUERY);
    setIsMobile(mediaQuery.matches);

    const handleChange = (event: MediaQueryListEvent) => {
      setIsMobile(event.matches);
    };

    mediaQuery.addEventListener("change", handleChange);
    return () => mediaQuery.removeEventListener("change", handleChange);
  }, []);

  return isMobile;
};

export default useIsMobile;
