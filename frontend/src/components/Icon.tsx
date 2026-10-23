import type { CSSProperties } from "react";

import { ICONS, type IconName } from "./constants";

/**
 * Icon set for the product — the same sprite the NL2SQL dashboard carries,
 * drawn inline rather than pulled from an icon library.
 *
 * Every path is on one 20×20 grid with `fill: none` and a `currentColor`
 * stroke, so weight and geometry match across the two products and a glyph
 * takes its colour from whatever it sits in. The roles this product needs that
 * the shared sprite does not cover — a book, a compass, a globe, a link, the
 * theme toggle's sun and moon — are drawn to the same grid and the same stroke
 * language.
 *
 * Call sites name icons by role rather than reaching for a glyph directly,
 * which keeps swapping any single one to a line in here.
 */

interface IconProps {
  name: IconName;
  /** Defaults to the 16px this product uses for inline icons. */
  className?: string;
  style?: CSSProperties;
  /**
   * Weight varies by context: 1.7 in the nav rail, 1.8 on buttons and cards,
   * 2.1–2.2 on a chip or a send button. 1.8 is the common case.
   */
  strokeWidth?: number;
}

export default function Icon({
  name,
  className = "size-4",
  style,
  strokeWidth = 1.8,
}: IconProps) {
  return (
    <svg
      viewBox="0 0 20 20"
      className={className}
      style={style}
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {ICONS[name]}
    </svg>
  );
}

export type { IconName };
