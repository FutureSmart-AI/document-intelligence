import { useCallback, useRef } from "react";

export function useCardJump<T extends HTMLElement = HTMLDivElement>() {
  const ref = useRef<T | null>(null);
  const jump = useCallback(() => {
    ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);
  return { ref, jump };
}
