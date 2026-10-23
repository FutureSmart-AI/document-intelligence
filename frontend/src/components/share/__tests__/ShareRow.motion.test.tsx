import { useState } from "react";

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ROW_CLOSE_MS, ROW_OPEN_MS } from "../constants";
import ShareRow from "../ShareRow";

/**
 * The row's motion contract, which is what keeps a re-render inside an open row
 * from being clipped and a closing row from vanishing mid-animation.
 *
 * jsdom implements no Web Animations API at all, so `Element.animate` is stubbed
 * with a recorder: the assertions are about which keyframes the row asks for,
 * that it always finishes by dropping its animations (leaving height back on
 * `auto`), and that the body survives until the closing animation says so.
 */

type Recorded = {
  target: Element;
  keyframes: Keyframe[];
  options: KeyframeAnimationOptions;
  animation: FakeAnimation;
};

class FakeAnimation {
  onfinish: (() => void) | null = null;
  cancelled = false;
  cancel() {
    this.cancelled = true;
  }
  finish() {
    this.onfinish?.();
  }
}

let recorded: Recorded[] = [];
let reduce = false;

beforeEach(() => {
  recorded = [];
  reduce = false;
  vi.stubGlobal(
    "matchMedia",
    (query: string) =>
      ({
        matches: query.includes("reduced-motion") ? reduce : false,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }) as unknown as MediaQueryList,
  );
  Element.prototype.animate = function (
    this: Element,
    keyframes: Keyframe[],
    options: KeyframeAnimationOptions,
  ) {
    const animation = new FakeAnimation();
    recorded.push({ target: this, keyframes, options, animation });
    return animation as unknown as Animation;
  } as typeof Element.prototype.animate;
});

afterEach(() => {
  vi.unstubAllGlobals();
  // jsdom ships no Web Animations API, so the stub is deleted rather than
  // restored — the next suite sees the same absence the product guards for.
  delete (Element.prototype as Partial<Element>).animate;
});

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <ShareRow
      id="share-widget"
      icon="chat"
      title="Chat bubble on your website"
      purpose="One script tag"
      open={open}
      onToggle={setOpen}
    >
      <p>Embed snippet</p>
    </ShareRow>
  );
}

const clip = () => document.getElementById("share-widget") as HTMLDivElement;
const heightAnimations = () =>
  recorded.filter((entry) => "height" in (entry.keyframes[0] ?? {}));

describe("ShareRow motion", () => {
  it("animates the clipper from zero to its content height, then lets height go", async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: /chat bubble/i }));

    const opening = heightAnimations().at(-1);
    expect(opening?.target).toBe(clip());
    expect(opening?.keyframes[0]).toMatchObject({ height: "0px" });
    expect(String(opening?.keyframes[1]?.height)).toMatch(/px$/);
    expect(opening?.options.duration).toBe(ROW_OPEN_MS);
    expect(opening?.options.fill).toBe("both");
    // No inline height is ever written, so nothing has to be cleaned up.
    expect(clip().style.height).toBe("");

    act(() => opening!.animation.finish());
    // Dropping the effect is what hands height back to `auto`.
    expect(opening!.animation.cancelled).toBe(true);
    expect(clip().style.height).toBe("");
    expect(screen.getByText("Embed snippet")).toBeInTheDocument();
  });

  it("fades the panel in behind the height, rather than only growing a box", async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: /chat bubble/i }));
    const arriving = recorded.find(
      (entry) => "opacity" in (entry.keyframes[0] ?? {}),
    );
    expect(arriving?.keyframes[0]).toMatchObject({
      opacity: 0,
      transform: "translateY(-6px)",
    });
    expect(arriving?.options.delay).toBe(40);
  });

  it("keeps the body mounted until the closing animation finishes", async () => {
    render(<Harness />);
    const header = screen.getByRole("button", { name: /chat bubble/i });
    await userEvent.click(header);
    act(() => heightAnimations().at(-1)!.animation.finish());

    await userEvent.click(header);
    const closing = heightAnimations().at(-1)!;
    expect(closing.keyframes[1]).toMatchObject({ height: "0px" });
    expect(closing.options.duration).toBe(ROW_CLOSE_MS);
    // Still on screen — there would be nothing left to animate otherwise.
    expect(screen.getByText("Embed snippet")).toBeInTheDocument();
    expect(clip().hidden).toBe(false);

    act(() => closing.animation.finish());
    await waitFor(() => expect(clip().hidden).toBe(true));
    expect(screen.queryByText("Embed snippet")).toBeNull();
  });

  it("asks for no animation at all under prefers-reduced-motion", async () => {
    reduce = true;
    render(<Harness />);
    const header = screen.getByRole("button", { name: /chat bubble/i });
    await userEvent.click(header);
    expect(screen.getByText("Embed snippet")).toBeInTheDocument();
    expect(recorded).toHaveLength(0);

    await userEvent.click(header);
    expect(screen.queryByText("Embed snippet")).toBeNull();
    expect(clip().hidden).toBe(true);
    expect(recorded).toHaveLength(0);
  });

  it("resumes from the height it was at when a close is interrupted", async () => {
    render(<Harness />);
    const header = screen.getByRole("button", { name: /chat bubble/i });
    await userEvent.click(header);
    act(() => heightAnimations().at(-1)!.animation.finish());
    await userEvent.click(header);
    const closing = heightAnimations().at(-1)!;

    // Halfway down: jsdom measures nothing, so the clipper is told what it is at.
    clip().getBoundingClientRect = () =>
      ({ height: 120 }) as unknown as DOMRect;
    const fadesBefore = recorded.filter(
      (entry) => "opacity" in (entry.keyframes[0] ?? {}),
    ).length;
    await userEvent.click(header);

    expect(closing.animation.cancelled).toBe(true);
    const reopening = heightAnimations().at(-1)!;
    // Not "0px": restarting from nothing is the jump this measurement prevents.
    expect(reopening.keyframes[0]).toMatchObject({ height: "120px" });
    // And content already on screen is not faded in a second time.
    expect(
      recorded.filter((entry) => "opacity" in (entry.keyframes[0] ?? {}))
        .length,
    ).toBe(fadesBefore);
  });

  it("cancels the animation in flight when the row is toggled again", async () => {
    render(<Harness />);
    const header = screen.getByRole("button", { name: /chat bubble/i });
    await userEvent.click(header);
    const opening = heightAnimations().at(-1)!;
    // Closed again before the opening animation landed.
    await userEvent.click(header);
    expect(opening.animation.cancelled).toBe(true);
    act(() => heightAnimations().at(-1)!.animation.finish());
    await waitFor(() => expect(clip().hidden).toBe(true));
  });
});
