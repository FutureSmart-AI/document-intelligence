"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

// Expanded-row cell for the query tables. The parent tables scroll
// horizontally (min-w + overflow-x-auto), and a plain `colSpan` cell stretches
// to the *full* scrollable table width — forcing the user to scroll right to
// see the tail of the detail panel. Instead we pin the content to the visible
// viewport: a sticky-left div sized to the scroll container's client width, so
// the detail always fits the screen no matter how wide the table is.
export default function ExpandedDetailCell({
  colSpan,
  children,
}: {
  colSpan: number;
  children: ReactNode;
}) {
  const tdRef = useRef<HTMLTableCellElement>(null);
  const [width, setWidth] = useState<number>();

  useEffect(() => {
    const td = tdRef.current;
    const scroller = td?.closest<HTMLElement>(".overflow-x-auto");
    if (!scroller) return undefined;
    const update = () => setWidth(scroller.clientWidth);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(scroller);
    return () => ro.disconnect();
  }, []);

  return (
    <td ref={tdRef} colSpan={colSpan} className="p-0">
      <div
        className="sticky left-0 px-4 py-4"
        style={{ width: width ? `${width}px` : undefined }}
      >
        {children}
      </div>
    </td>
  );
}
