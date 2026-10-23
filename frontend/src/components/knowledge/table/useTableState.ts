"use client";

import { useCallback, useMemo, useState } from "react";

import { COLUMN_GROUPS, DEFAULT_PAGE_SIZE } from "./constants";
import type {
  ColumnGroupKey,
  FilterOp,
  FilterRule,
  SortRule,
  TableColumn,
  TableQuery,
  VizMode,
} from "./types";

export interface TableStateOptions {
  /** Explore opens with the facet band already down; Data opens on the fields. */
  initialFacetOpen?: boolean;
  initialGroups?: Partial<Record<ColumnGroupKey, boolean>>;
}

let ruleSequence = 0;
/** Stable rule ids, so a row keeps focus while its value is being typed. */
function nextRuleId(): string {
  ruleSequence += 1;
  return `rule-${ruleSequence}`;
}

/**
 * Everything the one table remembers.
 *
 * Two axes on purpose, and they do not overwrite each other: `groups` is the
 * quick one (Show: All / Status only / Fields only) and `hidden` is the fine
 * one (the Columns panel). Hiding a group never touches a checkbox, so
 * All → Fields only → All puts back exactly the columns you had.
 */
export function useTableState(options: TableStateOptions = {}) {
  const [search, setSearch] = useState("");
  const [type, setType] = useState("all");
  const [status, setStatus] = useState("all");
  const [filters, setFilters] = useState<FilterRule[]>([]);
  const [sorts, setSorts] = useState<SortRule[]>([]);

  const [groups, setGroups] = useState<Record<ColumnGroupKey, boolean>>({
    doc: true,
    status: options.initialGroups?.status ?? true,
    fields: options.initialGroups?.fields ?? true,
  });
  /** Columns switched off by hand, by key. */
  const [hidden, setHidden] = useState<Record<string, boolean>>({});

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);

  const [compact, setCompact] = useState(false);
  const [fullScreen, setFullScreen] = useState(false);
  const [facetOpen, setFacetOpen] = useState(options.initialFacetOpen ?? false);
  const [facetField, setFacetField] = useState<string | null>(null);
  const [viz, setViz] = useState<VizMode>("bars");

  const query = useMemo<TableQuery>(
    () => ({ search, type, status, filters, sorts }),
    [search, type, status, filters, sorts],
  );

  /** Anything that changes which rows match sends the reader back to page 1. */
  const resetPage = useCallback(() => setPage(1), []);

  const updateSearch = useCallback(
    (value: string) => {
      setSearch(value);
      resetPage();
    },
    [resetPage],
  );

  const updateType = useCallback(
    (value: string) => {
      setType(value);
      resetPage();
    },
    [resetPage],
  );

  const updateStatus = useCallback(
    (value: string) => {
      setStatus(value);
      resetPage();
    },
    [resetPage],
  );

  const updateFilters = useCallback(
    (next: FilterRule[]) => {
      setFilters(next);
      resetPage();
    },
    [resetPage],
  );

  const addFilterRule = useCallback((field: string) => {
    setFilters((current) => [
      ...current,
      { id: nextRuleId(), field, op: "Contains" as FilterOp, value: "" },
    ]);
  }, []);

  /**
   * A facet click adds a value to this field's set, and clicking it again takes
   * it away.
   *
   * One rule per field, holding every value picked, because rules are ANDed:
   * two `Equals` rules on one column asked for a vendor that was both Acme and
   * Northwind and answered with an empty table, which is what picking a second
   * chip used to do. `Is any of` is the operator that makes a second click mean
   * "and also this one" — see `matchesRule`.
   *
   * The "no value" bucket rides in the same rule as a flag rather than as an
   * `Is empty` rule of its own, for exactly the same reason.
   */
  const toggleFacetValue = useCallback(
    (column: TableColumn, value: string, missing: boolean) => {
      setFilters((current) => {
        const index = current.findIndex(
          (rule) => rule.field === column.key && rule.op === "Is any of",
        );
        const existing = index > -1 ? current[index] : null;
        const values = existing?.values ?? [];
        const includeEmpty = existing?.includeEmpty ?? false;

        const next = missing
          ? { values, includeEmpty: !includeEmpty }
          : {
              values: values.includes(value)
                ? values.filter((entry) => entry !== value)
                : [...values, value],
              includeEmpty,
            };

        // Nothing selected is no rule at all, so the Filters badge counts what
        // is actually narrowing the table rather than an empty leftover.
        if (!next.values.length && !next.includeEmpty) {
          return index > -1
            ? current.filter((_, position) => position !== index)
            : current;
        }

        const rule: FilterRule = {
          id: existing?.id ?? nextRuleId(),
          field: column.key,
          op: "Is any of",
          value: "",
          ...next,
        };
        if (index > -1)
          return current.map((entry, position) =>
            position === index ? rule : entry,
          );
        return [...current, rule];
      });
      resetPage();
    },
    [resetPage],
  );

  const updateSorts = useCallback(
    (next: SortRule[]) => {
      setSorts(next);
      resetPage();
    },
    [resetPage],
  );

  /**
   * A header click is the single-column shorthand: ascending, then descending,
   * then off. It replaces a multi-rule sort rather than appending to it — the
   * Sort panel is where several rules are built, and a header that silently
   * became rule four of four is not a sort anyone asked for.
   */
  const toggleHeaderSort = useCallback(
    (field: string) => {
      setSorts((current) => {
        const only =
          current.length === 1 && current[0].field === field
            ? current[0]
            : null;
        if (!only) return [{ field, dir: 1 }];
        return only.dir === 1 ? [{ field, dir: -1 }] : [];
      });
      resetPage();
    },
    [resetPage],
  );

  /**
   * A group can be hidden, but not the last one: a grid of names with no
   * status and no fields is not a table of anything. `doc` is locked outright.
   */
  const toggleGroup = useCallback(
    (key: ColumnGroupKey) => {
      const group = COLUMN_GROUPS.find((candidate) => candidate.key === key);
      if (!group || group.locked) return false;
      const next = !groups[key];
      if (!next) {
        const anotherIsOn = COLUMN_GROUPS.some(
          (candidate) =>
            !candidate.locked && candidate.key !== key && groups[candidate.key],
        );
        if (!anotherIsOn) return false;
      }
      setGroups((current) => ({ ...current, [key]: next }));
      return true;
    },
    [groups],
  );

  const applyGroupPreset = useCallback(
    (preset: "all" | "status" | "fields") => {
      setGroups({
        doc: true,
        status: preset === "all" || preset === "status",
        fields: preset === "all" || preset === "fields",
      });
    },
    [],
  );

  const setColumnHidden = useCallback((key: string, value: boolean) => {
    setHidden((current) => ({ ...current, [key]: value }));
  }, []);

  const setManyHidden = useCallback((keys: string[], value: boolean) => {
    setHidden((current) => {
      const next = { ...current };
      for (const key of keys) next[key] = value;
      return next;
    });
  }, []);

  const clearAll = useCallback(() => {
    setSearch("");
    setType("all");
    setStatus("all");
    setFilters([]);
    resetPage();
  }, [resetPage]);

  return {
    query,
    search,
    type,
    status,
    filters,
    sorts,
    groups,
    hidden,
    page,
    pageSize,
    compact,
    fullScreen,
    facetOpen,
    facetField,
    viz,
    setPage,
    setPageSize: useCallback(
      (size: number) => {
        setPageSize(size);
        resetPage();
      },
      [resetPage],
    ),
    setCompact,
    setFullScreen,
    setFacetOpen,
    setFacetField,
    setViz,
    updateSearch,
    updateType,
    updateStatus,
    updateFilters,
    addFilterRule,
    toggleFacetValue,
    updateSorts,
    toggleHeaderSort,
    toggleGroup,
    applyGroupPreset,
    setColumnHidden,
    setManyHidden,
    clearAll,
  };
}

export type TableState = ReturnType<typeof useTableState>;

/** The columns actually on screen: group visible, and not switched off by hand. */
export function visibleColumns(
  columns: TableColumn[],
  groups: Record<ColumnGroupKey, boolean>,
  hidden: Record<string, boolean>,
): TableColumn[] {
  return columns.filter(
    (column) => groups[column.group] && (column.fixed || !hidden[column.key]),
  );
}
