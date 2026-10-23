import { formatValue } from "../utils";

interface TooltipPayloadEntry {
  name?: string;
  value?: number;
  color?: string;
  fill?: string;
  payload?: Record<string, any>;
}

interface ChartTooltipProps {
  active?: boolean;
  payload?: TooltipPayloadEntry[];
  label?: string | number;
  /** Pass total to display percentages (useful for pie charts) */
  total?: number;
}

export const ChartTooltip = ({
  active,
  payload,
  label,
  total,
}: ChartTooltipProps) => {
  if (!active || !payload?.length) return null;

  // For pie charts, the label comes from the payload entry name
  const displayLabel = label ?? payload[0]?.name;

  return (
    <div
      style={{
        background: "rgba(255, 255, 255, 0.98)",
        backdropFilter: "blur(8px)",
        borderRadius: 12,
        border: "1px solid #e2e8f0",
        boxShadow:
          "0 4px 16px rgba(0, 0, 0, 0.08), 0 1px 3px rgba(0, 0, 0, 0.04)",
        padding: "10px 14px",
        minWidth: 150,
      }}
    >
      {displayLabel != null && (
        <p
          style={{
            margin: "0 0 8px 0",
            fontSize: 13,
            fontWeight: 600,
            color: "#1e293b",
            borderBottom: "1px solid #f1f5f9",
            paddingBottom: 7,
            lineHeight: 1.3,
          }}
        >
          {displayLabel}
        </p>
      )}
      {payload.map((entry, i) => {
        const color = entry.color || entry.fill || "#6366f1";
        const val = entry.value ?? 0;
        const pct =
          total && total > 0 ? ` (${((val / total) * 100).toFixed(1)}%)` : "";

        return (
          <div
            key={`${entry.name}-${i}`}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 16,
              padding: "3px 0",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span
                style={{
                  width: 9,
                  height: 9,
                  borderRadius: "50%",
                  background: color,
                  display: "inline-block",
                  flexShrink: 0,
                }}
              />
              <span style={{ fontSize: 12, color: "#64748b", lineHeight: 1.3 }}>
                {entry.name}
              </span>
            </div>
            <span
              style={{
                fontSize: 13,
                fontWeight: 600,
                color: "#1e293b",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {typeof val === "number" ? formatValue(val) : val}
              {pct}
            </span>
          </div>
        );
      })}
    </div>
  );
};
