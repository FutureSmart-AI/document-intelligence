interface CustomXAxisTickProps {
  x?: number;
  y?: number;
  payload?: {
    value: string | number;
  };
  maxLabelLength?: number;
  angle?: number;
  fontSize?: number;
}

export const CustomXAxisTick = ({
  x,
  y,
  payload,
  maxLabelLength = 0,
  angle = -30,
  fontSize = 12,
}: CustomXAxisTickProps) => {
  if (!payload || !payload.value) return null;

  let display = String(payload.value);
  if (maxLabelLength > 0 && display.length > maxLabelLength) {
    display = display.slice(0, maxLabelLength) + "\u2026";
  }

  const textAnchor = angle === 0 ? "middle" : "end";

  return (
    <g transform={`translate(${x},${y})`}>
      <text
        x={0}
        y={0}
        dy={16}
        textAnchor={textAnchor}
        fill="#94a3b8"
        transform={angle !== 0 ? `rotate(${angle})` : undefined}
        style={{ fontSize, fontWeight: 400 }}
      >
        {display}
      </text>
    </g>
  );
};
