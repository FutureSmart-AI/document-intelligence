import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FieldsPane from "../FieldsPane";

/**
 * The switch above the fields, and what it decides.
 *
 * `enrichment_enabled` is the whole of it: off, the collection has no schema to
 * show at all, and the panel is not mounted. On, this product mounts its own
 * fields panel rather than the platform's enrichment page — see
 * `DocumentFieldsPanel` for why the two exist side by side.
 */

const props = vi.hoisted(() => ({ last: {} as Record<string, unknown> }));

vi.mock("../fields/DocumentFieldsPanel", () => ({
  default: (given: Record<string, unknown>) => {
    props.last = given;
    return <div data-testid="fields-panel" />;
  },
}));

vi.mock("@/platform/components/ToastContext", () => ({
  useToast: () => ({
    toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  }),
}));

let config: Record<string, unknown> | undefined;

vi.mock("@/data/knowledgeConfig", () => ({
  useKnowledgeSettings: () => ({ data: config, isLoading: false }),
  useSaveKnowledgeSettings: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

const K = "5b6a1f2e-2c4b-4a9d-9a1e-2f0c7d8e4b31";

describe("FieldsPane", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    props.last = {};
    config = { enrichment_enabled: true };
  });

  it("shows this product's own fields panel for the collection", () => {
    render(<FieldsPane knowledgeId={K} />);

    expect(screen.getByTestId("fields-panel")).toBeInTheDocument();
    expect(props.last).toMatchObject({
      knowledgeId: K,
      documentMetadataEnabled: true,
    });
  });

  /** Extraction off means there is no schema to show at all. */
  it("shows no fields panel when extraction is off", () => {
    config = { enrichment_enabled: false };
    render(<FieldsPane knowledgeId={K} />);

    expect(screen.queryByTestId("fields-panel")).not.toBeInTheDocument();
  });
});
