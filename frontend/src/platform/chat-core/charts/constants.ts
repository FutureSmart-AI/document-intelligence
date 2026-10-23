/** Vibrant, accessible color palette with good contrast between adjacent colors */
export const DEFAULT_COLORS = [
  "#6366f1", // indigo
  "#f43f5e", // rose
  "#10b981", // emerald
  "#f59e0b", // amber
  "#8b5cf6", // violet
  "#06b6d4", // cyan
  "#f97316", // orange
  "#ec4899", // pink
  "#14b8a6", // teal
  "#64748b", // slate
];

export const CHART_MARGINS = {
  top: 20,
  right: 20,
  left: 8,
  bottom: 20,
};

/** Shared axis tick styling */
export const AXIS_TICK_STYLE = {
  fontSize: 12,
  fill: "#94a3b8",
  fontWeight: 400 as const,
};

/** Shared axis label styling */
export const AXIS_LABEL_STYLE = {
  fontSize: 13,
  fill: "#475569",
  fontWeight: 600 as const,
};

/** Shared cartesian grid props */
export const GRID_PROPS = {
  stroke: "#f1f5f9",
  strokeDasharray: undefined as undefined,
  vertical: false,
} as const;
