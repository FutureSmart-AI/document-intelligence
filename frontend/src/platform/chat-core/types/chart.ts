export type PrimitiveValue = string | number;
export type DataRecord = Record<string, PrimitiveValue>;

export interface ChartData {
  data:
    | { x: number[]; y: number[]; [key: string]: number[] | string[] }
    | Array<DataRecord>
    | null;
  metadata?: {
    title?: string;
    x_axis?: { label?: string; field?: string };
    y_axis?: { label?: string; field?: string };
    point?: { color?: string; size?: number };
    trend_line?: { show?: boolean; color?: string };
    legend?: {
      show?: boolean;
      position?: string;
      title?: string;
      items?: string[];
    };
    colors?: string[] | Record<string, string>;
    series_labels?: string[];
    group?: { field?: string };
    chart_orientation?: "horizontal" | "vertical";
  };
}

export interface ChatChartProps {
  chartData: ChartData | null | undefined;
  chartSuggestion: string;
  className?: string;
}

export interface NormalizedData {
  data: DataRecord[];
  xField: string;
  yField: string;
  series: string[];
}

export interface ResolvedAxes {
  x: { label: string; field: string };
  y: { label: string; field: string };
}

export interface XAxisLayout {
  angle: number;
  maxLabelLength: number;
  height: number;
  fontSize: number;
}
