import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ChartDataframe } from "../chartDataframe";
import DiResultCard from "../DiResultCard";

vi.mock("@/platform/chat-core/charts", () => ({
  ChatChart: () => <div data-testid="chart" />,
}));

const ROWS = [
  { vendor: "Acme", renewals: 120 },
  { vendor: "Bolt", renewals: 80 },
  { vendor: "Cove", renewals: 60 },
  { vendor: "Dune", renewals: 40 },
  { vendor: "Eave", renewals: 20 },
  { vendor: "Fern", renewals: 10 },
];

const frame = (over: Partial<ChartDataframe> = {}): ChartDataframe => ({
  columns: ["vendor", "renewals"],
  rows: ROWS,
  ...over,
});

const CHART = {
  chartData: { data: [{ label: "Acme", value: 120 }] } as never,
  chartSuggestion: "Bar Chart",
};

describe("DiResultCard", () => {
  it("says how many rows the answer's frame held", () => {
    render(<DiResultCard dataframe={frame()} chart={null} />);
    expect(screen.getByText("6 rows")).toBeInTheDocument();
  });

  it("paginates at five rows and moves between pages", () => {
    render(<DiResultCard dataframe={frame()} chart={null} />);
    expect(screen.getByText("Showing 1–5 of 6")).toBeInTheDocument();
    expect(screen.queryByText("Fern")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Showing 6–6 of 6")).toBeInTheDocument();
    expect(screen.getByText("Fern")).toBeInTheDocument();
  });

  it("filters rows across the visible columns", () => {
    render(<DiResultCard dataframe={frame()} chart={null} />);
    fireEvent.change(screen.getByLabelText("Filter rows"), {
      target: { value: "Acme" },
    });
    expect(screen.getByText("Acme")).toBeInTheDocument();
    expect(screen.queryByText("Bolt")).not.toBeInTheDocument();
    expect(screen.getByText(/filtered from 6/)).toBeInTheDocument();
    // The card's own count reports the filter, as in the artifact.
    expect(screen.getByText("1 of 6 rows")).toBeInTheDocument();
  });

  it("hides a column from the columns menu, and keeps the last one", () => {
    render(<DiResultCard dataframe={frame()} chart={null} />);
    fireEvent.click(screen.getByRole("button", { name: /Columns/ }));

    fireEvent.click(screen.getByRole("button", { name: "Hide vendor" }));
    expect(
      screen.queryByRole("columnheader", { name: /vendor/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Hide renewals" }),
    ).toBeDisabled();
  });

  it("sorts by a column and flips direction on a second click", () => {
    render(<DiResultCard dataframe={frame()} chart={null} />);
    const header = screen.getByRole("button", { name: /renewals/ });

    fireEvent.click(header);
    expect(
      within(screen.getAllByRole("row")[1]).getByText("10"),
    ).toBeInTheDocument();

    fireEvent.click(header);
    expect(
      within(screen.getAllByRole("row")[1]).getByText("120"),
    ).toBeInTheDocument();
  });

  it("offers the rows and the plot as two tabs of one result", () => {
    render(<DiResultCard dataframe={frame()} chart={CHART} />);
    // A charted answer opens on its plot, as the artifact's strip does.
    expect(screen.getByTestId("chart")).toBeInTheDocument();
    expect(screen.getByText("Bar Chart")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /Data/ }));
    expect(screen.queryByTestId("chart")).not.toBeInTheDocument();
    expect(screen.getByText("Acme")).toBeInTheDocument();
  });

  it("puts the passages behind the answer on the same strip", () => {
    render(
      <DiResultCard
        dataframe={frame()}
        chart={null}
        sources={[
          { title: "ada.pdf", page: 2, content: "Eight years of engines." },
        ]}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: /Sources/ }));
    expect(screen.getByText("ada.pdf")).toBeInTheDocument();
    expect(screen.getByText("p. 2")).toBeInTheDocument();
    expect(screen.getByText(/Eight years of engines/)).toBeInTheDocument();
  });

  it("reads the passage from the field the knowledge service fills", () => {
    // `extractSources` names it `context`; nothing on a knowledge answer
    // carries `content` or `text`, which the old evidence panel read alone.
    render(
      <DiResultCard
        dataframe={null}
        chart={null}
        sources={[{ file_name: "priya.pdf", context: "Six years of Python." }]}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: /Sources/ }));
    expect(screen.getByText(/Six years of Python/)).toBeInTheDocument();
  });

  it("keeps the payload behind a click rather than opening on it", () => {
    render(
      <DiResultCard
        dataframe={null}
        chart={null}
        raw={{ ok: true }}
        rawEnabled
      />,
    );
    // Nothing else to show, so the card opens on no tab at all.
    expect(screen.queryByText(/"ok"/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /Raw/ }));
    expect(screen.getByText(/"ok": true/)).toBeInTheDocument();
  });

  it("opens straight onto a plot that has no frame behind it", () => {
    render(<DiResultCard dataframe={null} chart={CHART} />);
    expect(screen.getByTestId("chart")).toBeInTheDocument();
    // No row count, no filter, no export — so no toolbar at all.
    expect(screen.queryByLabelText("Filter rows")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "CSV" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  });

  it("draws nothing for an answer that produced none of them", () => {
    const { container } = render(
      <DiResultCard dataframe={null} chart={null} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
