import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  comma,
  compact,
  Donut,
  Kpi,
  LineChart,
  money,
  nextSort,
  QuotaBar,
  Rail,
  Segmented,
  sortRows,
  type SortState,
} from "../UsageBits";

/**
 * The Usage screen's furniture.
 *
 * Worth testing separately from the screen because these are the pieces that
 * turn a number into a claim: an abbreviation that rounds the wrong way, a
 * quota bar that draws a limit where the service said there is none, or a sort
 * that mutates its input all say something false without erroring.
 */

describe("number formatting", () => {
  it("abbreviates only above ten thousand, and keeps thousands readable", () => {
    expect(compact(0)).toBe("0");
    expect(compact(999)).toBe("999");
    // Four digits stay exact — "1.0K" loses precision for no space saved.
    expect(compact(1_000)).toBe("1,000");
    expect(compact(9_999)).toBe("9,999");
    expect(compact(10_000)).toBe("10.0K");
    expect(compact(12_400)).toBe("12.4K");
    expect(compact(1_200_000)).toBe("1.2M");
  });

  it("survives the shapes an empty account answers with", () => {
    expect(compact(undefined as unknown as number)).toBe("0");
    expect(comma(undefined as unknown as number)).toBe("0");
    expect(money(undefined as unknown as number)).toBe("$0.00");
  });

  /**
   * A real window can bill a fraction of a cent. `$0.00` beside a thousand
   * requests reads as "free", which is a different claim from "very cheap".
   */
  it("shows sub-cent cost rather than rounding it to nothing", () => {
    expect(money(0)).toBe("$0.00");
    expect(money(0.0004)).toBe("$0.0004");
    expect(money(0.009)).toBe("$0.0090");
    expect(money(0.01)).toBe("$0.01");
    expect(money(1.2)).toBe("$1.20");
  });
});

describe("column sorting", () => {
  it("cycles ascending, descending, then back to unsorted", () => {
    let sort: SortState<"cost"> | null = null;
    sort = nextSort(sort, "cost");
    expect(sort).toEqual({ key: "cost", dir: "asc" });
    sort = nextSort(sort, "cost");
    expect(sort).toEqual({ key: "cost", dir: "desc" });
    // The third click clears it rather than starting over.
    expect(nextSort(sort, "cost")).toBeNull();
  });

  it("starts a different column fresh, ascending", () => {
    expect(nextSort({ key: "cost", dir: "desc" }, "name")).toEqual({
      key: "name",
      dir: "asc",
    });
  });

  it("sorts numbers numerically and names alphabetically", () => {
    const rows = [
      { name: "B", cost: 2 },
      { name: "A", cost: 10 },
    ];
    expect(
      sortRows(rows, { key: "cost", dir: "asc" }).map((row) => row.cost),
    ).toEqual([2, 10]);
    // Not "10" < "2" — a string compare would put 10 first.
    expect(
      sortRows(rows, { key: "cost", dir: "desc" }).map((row) => row.cost),
    ).toEqual([10, 2]);
    expect(
      sortRows(rows, { key: "name", dir: "asc" }).map((row) => row.name),
    ).toEqual(["A", "B"]);
  });

  it("leaves the caller's rows untouched", () => {
    const rows = [{ name: "B" }, { name: "A" }];
    const sorted = sortRows(rows, { key: "name", dir: "asc" });
    expect(rows.map((row) => row.name)).toEqual(["B", "A"]);
    expect(sorted).not.toBe(rows);
  });

  it("returns the rows as given when nothing is sorted", () => {
    const rows = [{ name: "B" }, { name: "A" }];
    expect(sortRows(rows, null)).toBe(rows);
  });
});

describe("QuotaBar", () => {
  /**
   * `null` is the service's word for unlimited. Drawing that as a full bar
   * ("you are at your limit") or an empty one against a number are both
   * misreadings, so it is labelled instead.
   */
  it("says unlimited rather than drawing a quota that does not exist", () => {
    render(<QuotaBar label="Conversations" used={318} limit={null} />);
    expect(screen.getByText("318 of Unlimited")).toBeInTheDocument();
    expect(screen.getByText("No limit on this account")).toBeInTheDocument();
    expect(screen.queryByText(/% used/)).not.toBeInTheDocument();
  });

  it("reports the share used against a real limit", () => {
    render(<QuotaBar label="Messages" used={2_500} limit={10_000} />);
    expect(screen.getByText("2,500 of 10,000")).toBeInTheDocument();
    expect(screen.getByText("25% used")).toBeInTheDocument();
  });

  it("never draws past full when usage has overrun the limit", () => {
    const { container } = render(
      <QuotaBar label="Messages" used={150} limit={100} />,
    );
    expect(screen.getByText("100% used")).toBeInTheDocument();
    const fill = container.querySelector("span[style]") as HTMLElement;
    expect(fill.style.width).toBe("100%");
  });

  it("holds the figure back while it is still being read", () => {
    render(<QuotaBar label="Messages" used={0} limit={10} loading />);
    expect(screen.getByText("— of 10")).toBeInTheDocument();
  });
});

describe("Kpi", () => {
  it("shows a rise and a fall differently", () => {
    const { rerender } = render(
      <Kpi label="Pages read" value="60" delta={8.4} foot="vs. previous" />,
    );
    expect(screen.getByText("▲ 8.4%")).toBeInTheDocument();

    rerender(
      <Kpi label="Pages read" value="60" delta={-3.2} foot="vs. previous" />,
    );
    expect(screen.getByText("▼ 3.2%")).toBeInTheDocument();
  });

  /** No previous period means no comparison — not a 100% rise. */
  it("shows no change at all when there is nothing to compare with", () => {
    render(<Kpi label="Pages read" value="60" delta={null} foot="x" />);
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it("shows a dash, and no stale delta, while loading", () => {
    render(<Kpi label="Pages read" value="60" delta={8.4} foot="x" loading />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByText(/8.4%/)).not.toBeInTheDocument();
  });
});

describe("the period control and tabs", () => {
  it("marks the current period and reports a change", async () => {
    const onChange = vi.fn();
    render(
      <Segmented
        label="Reporting period"
        options={[
          { id: "7d", label: "Last 7 days" },
          { id: "30d", label: "Last 30 days" },
        ]}
        value="30d"
        onChange={onChange}
      />,
    );

    expect(
      screen.getByRole("button", { name: "Last 30 days" }),
    ).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "Last 7 days" }));
    expect(onChange).toHaveBeenCalledWith("7d");
  });

  it("marks the rail's current section for assistive technology", async () => {
    const onChange = vi.fn();
    render(
      <Rail
        label="Usage sections"
        heading="Usage"
        items={[
          { id: "overview", label: "Overview", icon: null },
          { id: "collections", label: "Collections", icon: null },
        ]}
        value="overview"
        onChange={onChange}
      />,
    );

    expect(screen.getByRole("button", { name: "Overview" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(
      screen.getByRole("button", { name: "Collections" }),
    ).not.toHaveAttribute("aria-current");
    await userEvent.click(screen.getByRole("button", { name: "Collections" }));
    expect(onChange).toHaveBeenCalledWith("collections");
  });
});

describe("LineChart", () => {
  it("plots one point per bucket and names the exact figure on each", () => {
    const { container } = render(
      <LineChart
        label="Total tokens over last 30 days"
        points={[
          { label: "08-18", value: 10 },
          { label: "08-19", value: 30 },
          { label: "08-20", value: 20 },
        ]}
      />,
    );

    expect(container.querySelectorAll("circle")).toHaveLength(3);
    // The hover figure hangs off each dot, so the middle bucket is readable
    // even though only the ends are labelled on the axis.
    const hovers = Array.from(container.querySelectorAll("title")).map(
      (title) => title.textContent,
    );
    expect(hovers).toContain("30 · 08-19");
    // Only the ends of the axis are labelled, not every bucket.
    expect(screen.getByText("08-18")).toBeInTheDocument();
    expect(screen.getByText("08-20")).toBeInTheDocument();
    expect(screen.queryByText("08-19")).not.toBeInTheDocument();
  });

  /** An all-zero window must still draw, rather than divide by a zero ceiling. */
  it("draws a window with nothing in it", () => {
    const { container } = render(
      <LineChart
        label="Cost over last 7 days"
        points={[
          { label: "08-18", value: 0 },
          { label: "08-19", value: 0 },
        ]}
      />,
    );
    expect(container.querySelector("path")).toBeInTheDocument();
  });
});

describe("Donut", () => {
  it("ranks the slices and folds everything past the fourth into Other", () => {
    const { container } = render(
      <Donut
        label="Estimated cost by service type"
        formatValue={money}
        slices={[
          { label: "a", value: 50 },
          { label: "b", value: 25 },
          { label: "c", value: 10 },
          { label: "d", value: 8 },
          { label: "e", value: 4 },
          { label: "f", value: 3 },
          { label: "gone", value: 0 },
        ]}
      />,
    );

    // Five arcs: the four largest, then one for the remainder.
    expect(screen.getByText("Other")).toBeInTheDocument();
    const arcs = Array.from(container.querySelectorAll("title")).map(
      (title) => title.textContent,
    );
    expect(arcs).toEqual([
      "a · 50%",
      "b · 25%",
      "c · 10%",
      "d · 8%",
      "Other · 7%",
    ]);
    // A zero-valued service is not drawn at all.
    expect(screen.queryByText("gone")).not.toBeInTheDocument();
  });
});
