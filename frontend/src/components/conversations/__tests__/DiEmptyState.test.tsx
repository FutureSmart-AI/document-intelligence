import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import DiEmptyState from "../DiEmptyState";

describe("DiEmptyState", () => {
  it("names the collection the questions are answered from", () => {
    render(
      <DiEmptyState
        collectionName="Vendor contracts"
        suggestions={[]}
        onPick={() => {}}
      />,
    );
    expect(screen.getByText("Vendor contracts")).toBeInTheDocument();
  });

  it("reads sensibly before the collection is known", () => {
    render(<DiEmptyState suggestions={[]} onPick={() => {}} />);
    expect(screen.getByText(/your collection/)).toBeInTheDocument();
  });

  it("offers the collection's starter questions and hands one back", () => {
    const onPick = vi.fn();
    render(
      <DiEmptyState
        suggestions={["Which contracts renew in March?"]}
        onPick={onPick}
      />,
    );
    expect(screen.getByText("Try one of these")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Which contracts renew in March?" }),
    );
    expect(onPick).toHaveBeenCalledWith("Which contracts renew in March?");
  });

  it("omits the suggestions block when the collection configured none", () => {
    render(<DiEmptyState suggestions={[]} onPick={() => {}} />);
    expect(screen.queryByText("Try one of these")).not.toBeInTheDocument();
  });
});
