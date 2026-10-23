import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ConversationRow } from "@/data/conversations";

import DiSessionSidebar from "../DiSessionSidebar";

const row = (over: Partial<ConversationRow> = {}): ConversationRow => ({
  session_id: "s1",
  title: "Which contracts renew in March?",
  exchange_count: 3,
  user_id: null,
  user_email: null,
  user_name: null,
  source: "dashboard",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  ...over,
});

const SESSIONS = [
  row(),
  row({ session_id: "s2", title: "Notice periods", exchange_count: 1 }),
];

const props = {
  sessions: SESSIONS,
  isLoading: false,
  currentId: null,
  onSelect: () => {},
  onNewChat: () => {},
  tabs: [],
  query: "",
  onQueryChange: () => {},
  page: 1,
  totalPages: 1,
  onPageChange: () => {},
};

describe("DiSessionSidebar", () => {
  it("groups the conversations by when they were last answered", () => {
    render(<DiSessionSidebar {...props} />);
    expect(screen.getByText("Today")).toBeInTheDocument();
    expect(
      screen.getByText("Which contracts renew in March?"),
    ).toBeInTheDocument();
    expect(screen.getByText("Notice periods")).toBeInTheDocument();
  });

  it("keeps a conversation to one line, as the Database Agent's rail does", () => {
    render(<DiSessionSidebar {...props} />);
    expect(screen.queryByText(/3 questions/)).not.toBeInTheDocument();
  });

  it("starts a new chat from the rail", () => {
    const onNewChat = vi.fn();
    render(<DiSessionSidebar {...props} onNewChat={onNewChat} />);
    fireEvent.click(screen.getByRole("button", { name: "New chat" }));
    expect(onNewChat).toHaveBeenCalled();
  });

  it("hands the search box's text back rather than filtering in the browser", () => {
    const onQueryChange = vi.fn();
    render(<DiSessionSidebar {...props} onQueryChange={onQueryChange} />);
    fireEvent.change(screen.getByLabelText("Search conversations"), {
      target: { value: "renew" },
    });
    expect(onQueryChange).toHaveBeenCalledWith("renew");
    // Both rows are still drawn: the service does the narrowing.
    expect(screen.getByText("Notice periods")).toBeInTheDocument();
  });

  it("says a search matched nothing in the words of the search", () => {
    render(<DiSessionSidebar {...props} sessions={[]} query="renew" />);
    expect(
      screen.getByText("No conversation matches “renew”."),
    ).toBeInTheDocument();
  });

  it("offers no delete on a list the reader may only watch", () => {
    render(<DiSessionSidebar {...props} />);
    expect(
      screen.queryByRole("button", { name: /^Delete / }),
    ).not.toBeInTheDocument();
  });

  it("asks before deleting, and does nothing when the answer is no", () => {
    const onDelete = vi.fn();
    render(<DiSessionSidebar {...props} onDelete={onDelete} />);

    fireEvent.click(
      screen.getByRole("button", {
        name: 'Delete "Which contracts renew in March?"',
      }),
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Keep it" }));
    expect(onDelete).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("deletes the conversation the reader confirmed", () => {
    const onDelete = vi.fn();
    render(<DiSessionSidebar {...props} onDelete={onDelete} />);

    fireEvent.click(
      screen.getByRole("button", { name: 'Delete "Notice periods"' }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    expect(onDelete).toHaveBeenCalledTimes(1);
    expect(onDelete.mock.calls[0][0].session_id).toBe("s2");
  });

  it("names the collection and its documents in the footer", () => {
    render(
      <DiSessionSidebar
        {...props}
        collection={{
          name: "Vendor contracts",
          documentCount: 12,
          answering: true,
        }}
      />,
    );
    expect(screen.getByText("Vendor contracts")).toBeInTheDocument();
    expect(screen.getByText("Answering · 12 documents")).toBeInTheDocument();
  });

  it("says so when the collection has stopped answering", () => {
    render(
      <DiSessionSidebar
        {...props}
        collection={{ name: "Vendor contracts", answering: false }}
      />,
    );
    expect(screen.getByText("Paused")).toBeInTheDocument();
  });

  it("pages only when the service has more than one page", () => {
    const { rerender } = render(<DiSessionSidebar {...props} />);
    expect(
      screen.queryByRole("button", { name: "Next" }),
    ).not.toBeInTheDocument();

    const onPageChange = vi.fn();
    rerender(
      <DiSessionSidebar
        {...props}
        page={1}
        totalPages={3}
        onPageChange={onPageChange}
      />,
    );
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(onPageChange).toHaveBeenCalledWith(2);
  });

  it("names whose thread it is, and how long, only on a list of more than one account", () => {
    const shared = [row({ user_name: "Ada Lovelace" })];
    const { rerender } = render(
      <DiSessionSidebar {...props} sessions={shared} />,
    );
    expect(screen.queryByText(/Ada Lovelace/)).not.toBeInTheDocument();

    rerender(<DiSessionSidebar {...props} sessions={shared} showOwner />);
    expect(screen.getByText("Ada Lovelace · 3 questions")).toBeInTheDocument();
  });
});
