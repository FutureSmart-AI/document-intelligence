import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import axios from "@/platform/utils/axios";

import { distinctTypes, useFieldTemplates } from "../fieldCatalog";

vi.mock("@/platform/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const mockedGet = vi.mocked(axios.get);

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

/** As published: the preset-less kind arrives first. */
const TEMPLATES = [
  { key: "default", display_name: "Something else", fields: [] },
  {
    key: "resume",
    display_name: "Resumes / CVs",
    fields: [{ field_name: "candidate_name", field_type: "str" }],
  },
  {
    key: "invoice",
    display_name: "Invoices",
    fields: [{ field_name: "total_amount", field_type: "float" }],
  },
];

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useFieldTemplates", () => {
  /**
   * "Something else" is where you land when none of the kinds fit, so it reads
   * as the last resort and sits last — whatever order the service sends.
   */
  it("puts the kind with no preset last, and keeps the rest as published", async () => {
    mockedGet.mockResolvedValue({ data: { templates: TEMPLATES } });

    const { result } = renderHook(() => useFieldTemplates(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data?.map((entry) => entry.key)).toEqual([
      "resume",
      "invoice",
      "default",
    ]);
  });

  /** A preset-less kind the service names something else still sorts last. */
  it("recognises the preset-less kind by having no fields", async () => {
    mockedGet.mockResolvedValue({
      data: {
        templates: [
          { key: "anything_at_all", display_name: "Anything", fields: [] },
          TEMPLATES[1],
        ],
      },
    });

    const { result } = renderHook(() => useFieldTemplates(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data?.map((entry) => entry.key)).toEqual([
      "resume",
      "anything_at_all",
    ]);
  });
});

describe("distinctTypes", () => {
  const TYPES = [
    { field_type: "str", field_type_label: "Text", requires_schema: false },
    { field_type: "int", field_type_label: "Number", requires_schema: false },
    { field_type: "float", field_type_label: "Number", requires_schema: false },
  ];

  /**
   * Folding `float` into `int` under one "Number" meant picking Number always
   * sent `int`, and an `int` field keeps whole numbers only — a per-mile rate
   * of 0.18 came back as 0. Both stay, told apart in words.
   */
  it("keeps every wire type and tells apart the ones sharing a label", () => {
    expect(
      distinctTypes(TYPES).map((entry) => [
        entry.field_type,
        entry.field_type_label,
      ]),
    ).toEqual([
      ["str", "Text"],
      ["int", "Number (whole)"],
      ["float", "Number (decimal)"],
    ]);
  });

  it("names the wire value when it has no words of its own", () => {
    expect(
      distinctTypes([
        { field_type: "a", field_type_label: "Thing", requires_schema: false },
        { field_type: "b", field_type_label: "Thing", requires_schema: false },
      ]).map((entry) => entry.field_type_label),
    ).toEqual(["Thing (a)", "Thing (b)"]);
  });
});
