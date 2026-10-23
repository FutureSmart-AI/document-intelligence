import type React from "react";

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/platform/components/ToastContext";

import DiThread from "../DiThread";

vi.mock("@/platform/components/conversations/ChatFeedbackButtons", () => ({
  default: () => <div data-testid="feedback" />,
}));
vi.mock("@/platform/components/conversations/copy-to-clipboard", () => ({
  CopyToClipboard: () => <button type="button">Copy</button>,
}));
vi.mock("@/platform/chat-core/charts", () => ({
  ChatChart: () => <div data-testid="chart" />,
}));

/** An assistant message shaped as the knowledge stream hands one over. */
const ANSWER = {
  role: "assistant",
  chat_id: "chat-1",
  response: "Three candidates have more than five years of experience.",
  debug_info: { latency_ms: 2400 },
  data: {
    knowledge: {
      sources: [
        {
          title: "ada-lovelace.pdf",
          page: 2,
          content: "Eight years building analytical engines.",
        },
      ],
      chart_output: {
        type: "chart_response",
        chart_suggestion: "Pie Chart",
        chart_data: { data: [{ label: "5+ years", value: 3 }] },
        chart_generated: true,
      },
    },
  },
};

const MESSAGES = [
  { role: "user", message: "who has the most experience?", chat_id: "chat-1" },
  ANSWER,
];

const renderThread = (
  props: Partial<React.ComponentProps<typeof DiThread>> = {},
) =>
  render(
    <ToastProvider>
      <DiThread
        messages={MESSAGES}
        loading={false}
        streaming={false}
        sessionId="s1"
        chatId="chat-1"
        activeTab="newchat"
        knowledgeId="k1"
        showSources
        rawEnabled={false}
        onPickQuestion={() => {}}
        {...props}
      />
    </ToastProvider>,
  );

describe("DiThread", () => {
  it("marks the question with the reader's own avatar", () => {
    renderThread();
    expect(screen.getByTestId("asker-avatar")).toBeInTheDocument();
  });

  it("shows the question and the answer", () => {
    renderThread();
    expect(
      screen.getByText("who has the most experience?"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Three candidates/)).toBeInTheDocument();
  });

  it("reports the documents read, the chart and the timing", () => {
    renderThread();
    expect(screen.getByText("Read 1 document")).toBeInTheDocument();
    expect(screen.getByText("Built a chart")).toBeInTheDocument();
    expect(screen.getByText("Answered in")).toBeInTheDocument();
    expect(screen.getByText("2.40s")).toBeInTheDocument();
  });

  it("draws the chart the answer produced", () => {
    renderThread();
    expect(screen.getByTestId("chart")).toBeInTheDocument();
  });

  it("opens the Sources tab onto the cited passages", () => {
    renderThread();
    fireEvent.click(screen.getByRole("tab", { name: /Sources/ }));
    expect(screen.getByText("ada-lovelace.pdf")).toBeInTheDocument();
    expect(screen.getByText("p. 2")).toBeInTheDocument();
    expect(
      screen.getByText(/Eight years building analytical engines/),
    ).toBeInTheDocument();
  });

  it("hides the evidence when the collection does not show sources", () => {
    renderThread({ showSources: false });
    expect(screen.queryByText("Read 1 document")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: /Sources/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/Three candidates/)).toBeInTheDocument();
  });

  it("hides the raw payload unless the collection exposes it", () => {
    renderThread();
    expect(screen.queryByRole("tab", { name: /Raw/ })).not.toBeInTheDocument();
  });

  it("does not present a raw dump as the explanation", () => {
    // Measured on a live collection: an answer whose `knowledge_evidence`
    // carries only ids and scores has no readable passage, so the strip is
    // raw-only — and offering a Sources tab would claim an explanation the
    // JSON does not give.
    renderThread({
      rawEnabled: true,
      messages: [
        MESSAGES[0],
        {
          role: "assistant",
          chat_id: "chat-1",
          response: "Four candidates matched.",
          sources: null,
          data: {
            knowledge: {
              knowledge_evidence: [
                {
                  tool: "retrieve_chunks",
                  documents: [{ score: 0.4, object_id: "o1" }],
                },
              ],
            },
          },
        },
      ],
    });
    expect(
      screen.queryByRole("tab", { name: /Sources/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Raw/ })).toBeInTheDocument();
  });

  it("shows a live trace while the answer is still streaming", () => {
    renderThread({
      streaming: true,
      messages: [
        MESSAGES[0],
        { role: "assistant", response: "Three", chat_id: "chat-1" },
      ],
    });
    expect(screen.getByText("Reading your documents")).toBeInTheDocument();
    expect(screen.getByText("Writing the answer")).toBeInTheDocument();
  });

  it("offers the follow-ups the answer suggested", () => {
    renderThread({
      messages: [
        MESSAGES[0],
        { ...ANSWER, follow_up: ["Who has the least experience?"] },
      ],
    });
    expect(
      screen.getByRole("button", { name: "Who has the least experience?" }),
    ).toBeInTheDocument();
  });

  it("offers nothing to drill into while the answer is still streaming", () => {
    renderThread({ streaming: true, rawEnabled: true });
    expect(
      screen.queryByRole("tab", { name: /Raw|Sources/ }),
    ).not.toBeInTheDocument();
  });
});
