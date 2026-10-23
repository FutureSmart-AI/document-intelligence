import { describe, expect, it } from "vitest";

import { breachSentence, limitLabel, quotaErrorOf } from "../quotaError";

/**
 * The account-is-full refusal, as the knowledge service actually sends it.
 *
 * Copied from a live response: an account holding 10 of 10 agents being asked
 * for one more. Note `remaining_quota.agents` is already negative — headroom
 * arithmetic, which is why nothing here reads it.
 */
const body = {
  error: "quota_exceeded",
  message:
    "You have reached one of your account's usage limits. Delete unused items or upgrade your plan.",
  remaining_quota: {
    indexed_characters: -25212694,
    knowledge_items: -27261,
    agents: -1,
    conversations: 100,
    messages: 500,
  },
  would_exceed: [
    { limit_name: "max_agents", current: 10, limit: 10, requested: 1 },
  ],
};

const errorWith = (data: unknown) => ({ response: { status: 403, data } });

describe("quotaErrorOf", () => {
  it("reads the refusal off the response body", () => {
    const quota = quotaErrorOf(errorWith(body));
    expect(quota).not.toBeNull();
    expect(quota?.message).toMatch(
      /reached one of your account's usage limits/,
    );
    expect(quota?.breaches).toEqual([
      { limitName: "max_agents", current: 10, limit: 10, requested: 1 },
    ]);
  });

  it("reads it when FastAPI wraps it in `detail`", () => {
    expect(quotaErrorOf(errorWith({ detail: body }))?.breaches).toHaveLength(1);
  });

  it("is not a quota refusal when the body says something else", () => {
    expect(
      quotaErrorOf(errorWith({ detail: "Not your knowledge" })),
    ).toBeNull();
    expect(quotaErrorOf(new Error("Network Error"))).toBeNull();
    expect(quotaErrorOf(undefined)).toBeNull();
  });

  it("keeps the refusal when it names no row, and says only what it said", () => {
    const quota = quotaErrorOf(
      errorWith({ error: "quota_exceeded", message: "Out of quota." }),
    );
    expect(quota?.message).toBe("Out of quota.");
    expect(quota?.breaches).toEqual([]);
    expect(breachSentence(quota!)).toBe("");
  });

  it("falls back to our own sentence when the service sent none", () => {
    expect(quotaErrorOf(errorWith({ error: "quota_exceeded" }))?.message).toBe(
      "You have reached one of your account's usage limits.",
    );
  });
});

describe("limitLabel", () => {
  it("names allowances the way the Limits tab does", () => {
    expect(limitLabel("max_agents")).toBe("Agents");
    expect(limitLabel("max_indexed_characters")).toBe("Indexed characters");
    expect(limitLabel("max_llm_tokens_per_month")).toBe("LLM tokens");
    expect(limitLabel("max_messages_per_day")).toBe("Messages today");
  });
});

describe("breachSentence", () => {
  it("gives the figures of every row that refused", () => {
    expect(breachSentence(quotaErrorOf(errorWith(body))!)).toBe(
      "Agents: 10 of 10 used.",
    );
  });

  it("says a row is full when the figures are missing", () => {
    const quota = quotaErrorOf(
      errorWith({
        error: "quota_exceeded",
        would_exceed: [{ limit_name: "max_documents" }],
      }),
    );
    expect(breachSentence(quota!)).toBe("Documents is at its limit.");
  });
});
