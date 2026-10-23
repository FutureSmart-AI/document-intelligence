import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import JsonTree from "../JsonTree";

/**
 * The document panel's JSON, drawn like the agent UI's raw-document JSON that
 * the reference showed: coloured by kind, folding, and wrapping.
 */
const ROW = {
  object_id: "661d269b",
  fuel_rate__per_mile: 0.18,
  paid: false,
  currency: null,
  services: ["freight", "customs"],
  line_items: [{ description: "Haulage", amount: 81 }],
};

describe("JsonTree", () => {
  it("prints every key and value, the way JSON writes them", () => {
    const { container } = render(<JsonTree value={ROW} />);
    const text = container.textContent ?? "";
    expect(text).toContain('"object_id": "661d269b",');
    expect(text).toContain('"fuel_rate__per_mile": 0.18,');
    expect(text).toContain('"paid": false,');
    expect(text).toContain('"currency": null,');
    expect(text).toContain('"description": "Haulage",');
    // The last entry of an object takes no comma.
    expect(text).toContain('"amount": 81}');
  });

  it("colours a value by its kind", () => {
    render(<JsonTree value={ROW} />);
    expect(screen.getByText("0.18")).toHaveClass("text-success-text");
    expect(screen.getByText("false")).toHaveClass("text-warn-text");
    expect(screen.getByText('"661d269b"')).toHaveClass("text-danger-text");
    expect(screen.getByText('"object_id"')).toHaveClass("text-accent");
  });

  it("folds a list and says how much it hides", async () => {
    render(<JsonTree value={ROW} />);
    const fold = screen.getByRole("button", { name: "Fold services" });
    expect(fold).toHaveAttribute("aria-expanded", "true");
    await userEvent.click(fold);
    expect(
      screen.getByRole("button", { name: "Unfold services" }),
    ).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("2 items")).toBeInTheDocument();
    expect(screen.queryByText('"customs"')).not.toBeInTheDocument();
  });

  it("names a list item's fold after its place in the list", () => {
    render(<JsonTree value={ROW} />);
    expect(
      screen.getByRole("button", { name: "Fold line_items #1" }),
    ).toBeInTheDocument();
  });

  it("escapes a string rather than printing its raw quotes", () => {
    render(<JsonTree value={{ note: 'says "hi"' }} />);
    expect(screen.getByText('"says \\"hi\\""')).toBeInTheDocument();
  });
});
