import { useCallback, useEffect, useRef, useState } from "react";

type UseResizablePanelOptions = {
  /** localStorage key the chosen width is persisted under. */
  storageKey: string;
  defaultWidth: number;
  minWidth?: number;
  maxWidth?: number;
  /** Which edge carries the drag handle. A left-edge handle grows the panel
   * when dragged towards smaller clientX (right-side panels); a right-edge
   * handle grows it when dragged towards larger clientX (left sidebars). */
  edge?: "left" | "right";
};

const clamp = (value: number, min: number, max: number) =>
  Math.min(Math.max(value, min), max);

const readStoredWidth = (key: string): number | null => {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(key);
  if (raw === null) return null;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : null;
};

/** Pointer-driven width for a docked panel, persisted per `storageKey`.
 * Shared by the document preview panel and the dashboard sidebar so both
 * resize identically. */
export const useResizablePanel = ({
  storageKey,
  defaultWidth,
  minWidth = 280,
  maxWidth = 900,
  edge = "left",
}: UseResizablePanelOptions) => {
  const [width, setWidthState] = useState(() =>
    clamp(readStoredWidth(storageKey) ?? defaultWidth, minWidth, maxWidth),
  );
  const [isResizing, setIsResizing] = useState(false);
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);
  // Mirrors `width` so pointer handlers and the drag start read it without
  // re-subscribing on every pixel of movement.
  const widthRef = useRef(width);

  const persist = useCallback(
    (next: number) => {
      if (typeof window !== "undefined") {
        window.localStorage.setItem(storageKey, String(next));
      }
    },
    [storageKey],
  );

  const applyWidth = useCallback(
    (next: number) => {
      const clamped = clamp(next, minWidth, maxWidth);
      widthRef.current = clamped;
      setWidthState(clamped);
      return clamped;
    },
    [minWidth, maxWidth],
  );

  /** Sets and persists a width chosen outside a drag (keyboard, presets). */
  const setWidth = useCallback(
    (next: number) => {
      persist(applyWidth(next));
    },
    [applyWidth, persist],
  );

  const resetWidth = useCallback(() => {
    persist(applyWidth(defaultWidth));
  }, [applyWidth, defaultWidth, persist]);

  const startResize = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    dragRef.current = { startX: e.clientX, startWidth: widthRef.current };
    setIsResizing(true);
  }, []);

  useEffect(() => {
    if (!isResizing) return undefined;

    const handleMove = (e: PointerEvent | MouseEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      const delta =
        edge === "left" ? drag.startX - e.clientX : e.clientX - drag.startX;
      applyWidth(drag.startWidth + delta);
    };

    // Only the end of a drag is persisted, so a drag writes once instead of
    // once per pointer move.
    const handleUp = () => {
      setIsResizing(false);
      dragRef.current = null;
      persist(widthRef.current);
    };

    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp);
    window.addEventListener("pointercancel", handleUp);
    // Dragging over text would otherwise select it.
    const previousUserSelect = document.body.style.userSelect;
    document.body.style.userSelect = "none";

    return () => {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleUp);
      window.removeEventListener("pointercancel", handleUp);
      document.body.style.userSelect = previousUserSelect;
    };
  }, [isResizing, edge, applyWidth, persist]);

  return { width, setWidth, resetWidth, isResizing, startResize };
};

export default useResizablePanel;
