import { DataType } from "@/platform/types/data-view";

export interface FilterRule {
  id: string;
  field: string;
  originalField?: string;
  subcolumn?: string;
  operator:
    | "eq"
    | "ne"
    | "contains"
    | "not_contains"
    | "is_empty"
    | "is_not_empty"
    | "gte"
    | "lte"
    | "gt"
    | "lt"
    | "in";
  /** For `in`, treated as raw textarea text until Apply; otherwise scalar / number */
  value: string | number | string[];
}

export const OPERATOR_OPTIONS: {
  label: string;
  value: FilterRule["operator"];
}[] = [
  { label: "Equals", value: "eq" },
  { label: "Not equals", value: "ne" },
  { label: "Contains", value: "contains" },
  { label: "Not contains", value: "not_contains" },
  { label: "Is empty", value: "is_empty" },
  { label: "Is not empty", value: "is_not_empty" },
  { label: ">=", value: "gte" },
  { label: "<=", value: "lte" },
  { label: ">", value: "gt" },
  { label: "<", value: "lt" },
  { label: "In", value: "in" },
];

export const VALUE_REQUIRED_OPERATORS: FilterRule["operator"][] = [
  "eq",
  "ne",
  "contains",
  "not_contains",
  "gte",
  "lte",
  "gt",
  "lt",
  "in",
];

export const NUMERIC_TYPES = new Set<DataType>(["int", "float"]);
export const TEXT_TYPES = new Set<DataType>(["str", "List[str]"]);
