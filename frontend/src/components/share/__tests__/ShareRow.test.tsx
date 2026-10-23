import { useState, type ReactNode } from "react";

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import ShareRow from "../ShareRow";

/** The screen's own usage: open state is held above the row. */
function Harness({ control }: { control?: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <ShareRow
      id="share-widget"
      icon="chat"
      title="Chat bubble on your website"
      purpose="One script tag, a bubble in the corner of your own pages"
      state={{ label: "Needs a public surface", live: false }}
      control={control}
      open={open}
      onToggle={setOpen}
    >
      <p>Embed snippet</p>
    </ShareRow>
  );
}

describe("ShareRow", () => {
  it("keeps the body out of the DOM until the row is opened", async () => {
    render(<Harness />);
    const header = screen.getByRole("button", {
      name: /chat bubble on your website/i,
    });
    expect(header).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Embed snippet")).toBeNull();

    await userEvent.click(header);
    expect(header).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Embed snippet")).toBeInTheDocument();
  });

  it("states its purpose and its state while closed, so the row does not have to be opened to be read", () => {
    render(<Harness />);
    expect(
      screen.getByText(/one script tag, a bubble in the corner/i),
    ).toBeInTheDocument();
    expect(screen.getByText("Needs a public surface")).toBeInTheDocument();
  });

  it("does not expand when its own control is used", async () => {
    const onChange = vi.fn();
    render(
      <Harness
        control={
          <button type="button" onClick={onChange}>
            Reset
          </button>
        }
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(
      screen.getByRole("button", { name: /chat bubble on your website/i }),
    ).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Embed snippet")).toBeNull();
  });

  it("names the body region and wires it to its header", async () => {
    render(<Harness />);
    const header = screen.getByRole("button", {
      name: /chat bubble on your website/i,
    });
    await userEvent.click(header);
    const region = screen.getByRole("region", {
      name: "Chat bubble on your website",
    });
    expect(region.id).toBe(header.getAttribute("aria-controls"));
    expect(region.hidden).toBe(false);
  });
});
