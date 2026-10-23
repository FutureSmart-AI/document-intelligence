import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import DiChatHeader from "../DiChatHeader";

describe("DiChatHeader", () => {
  it("names the conversation and the collection it asks", () => {
    render(
      <DiChatHeader
        title="New conversation"
        collectionName="Vendor contracts"
        answering
      />,
    );
    expect(screen.getByText("New conversation")).toBeInTheDocument();
    expect(screen.getByText("Asking Vendor contracts")).toBeInTheDocument();
    expect(screen.getByText("Answering")).toBeInTheDocument();
  });

  it("says when the collection has stopped answering", () => {
    render(<DiChatHeader title="Conversation" answering={false} />);
    expect(screen.getByText("Paused")).toBeInTheDocument();
  });

  it("claims neither while the collection is still loading", () => {
    render(<DiChatHeader title="Conversation" answering={null} />);
    expect(screen.queryByText("Answering")).not.toBeInTheDocument();
    expect(screen.queryByText("Paused")).not.toBeInTheDocument();
  });

  it("toggles the documents panel, and reports whether it is open", () => {
    const onToggleDocuments = vi.fn();
    const { rerender } = render(
      <DiChatHeader
        title="Conversation"
        answering
        onToggleDocuments={onToggleDocuments}
      />,
    );
    const button = screen.getByRole("button", { name: "Documents" });
    expect(button).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(button);
    expect(onToggleDocuments).toHaveBeenCalled();

    rerender(
      <DiChatHeader
        title="Conversation"
        answering
        documentsOpen
        onToggleDocuments={onToggleDocuments}
      />,
    );
    expect(screen.getByRole("button", { name: "Documents" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("offers no documents button when the screen has nothing to open", () => {
    render(<DiChatHeader title="Conversation" answering />);
    expect(
      screen.queryByRole("button", { name: "Documents" }),
    ).not.toBeInTheDocument();
  });
});
