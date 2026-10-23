import { describe, expect, it } from "vitest";

import { parseQuestionGroups } from "../parseQuestionGroups";

/**
 * The uploaded question list is parsed in the browser, so this is where a
 * malformed file is caught. Both documented shapes are pinned, and so is the
 * behaviour that matters most: a file that cannot be read must throw a sentence
 * a reader can act on, not silently produce an empty report.
 */

describe("JSON", () => {
  it("reads the documented `query_groups` shape", () => {
    const groups = parseQuestionGroups(
      JSON.stringify({
        query_groups: [
          { type: "independent", queries: ["One?", "Two?"] },
          { type: "sequential", queries: ["Three?"] },
        ],
      }),
      "questions.json",
    );
    expect(groups).toEqual([
      { type: "independent", queries: ["One?", "Two?"] },
      { type: "sequential", queries: ["Three?"] },
    ]);
  });

  it("reads the artifact's longer `question_groups` sample too", () => {
    // The format panel has shown readers both shapes; a reader following either
    // one is following documentation they were given.
    const groups = parseQuestionGroups(
      JSON.stringify({
        question_groups: [
          {
            group_id: "g1",
            group_name: "Candidate mix",
            group_type: "sequential",
            questions: [{ question_id: "q1", question_text: "How many?" }],
          },
        ],
      }),
      "questions.json",
    );
    expect(groups).toEqual([{ type: "sequential", queries: ["How many?"] }]);
  });

  it("falls back to an independent group for an unknown type", () => {
    const groups = parseQuestionGroups(
      JSON.stringify({ query_groups: [{ type: "parallel", queries: ["A?"] }] }),
      "q.json",
    );
    expect(groups[0].type).toBe("independent");
  });

  it("drops blank questions and empty groups", () => {
    const groups = parseQuestionGroups(
      JSON.stringify({
        query_groups: [
          { type: "independent", queries: ["  ", ""] },
          { type: "independent", queries: ["Real?"] },
        ],
      }),
      "q.json",
    );
    expect(groups).toEqual([{ type: "independent", queries: ["Real?"] }]);
  });

  it("throws on JSON holding no questions", () => {
    expect(() =>
      parseQuestionGroups(JSON.stringify({ query_groups: [] }), "q.json"),
    ).toThrow(/no questions/i);
  });

  it("throws on a body that is not JSON at all", () => {
    expect(() => parseQuestionGroups("{not json", "q.json")).toThrow(
      /not valid JSON/i,
    );
  });
});

describe("CSV", () => {
  const csv = `group_id,group_name,group_type,question_id,question_text
g1,Candidate mix,independent,q1,"How many candidates per role?"
g1,Candidate mix,independent,q2,"Which tools, exactly, do they know?"
g2,Follow-up,sequential,q3,"Who is available now?"`;

  it("groups rows by group_id, in first-seen order", () => {
    expect(parseQuestionGroups(csv, "questions.csv")).toEqual([
      {
        type: "independent",
        queries: [
          "How many candidates per role?",
          "Which tools, exactly, do they know?",
        ],
      },
      { type: "sequential", queries: ["Who is available now?"] },
    ]);
  });

  it("keeps a comma that sits inside a quoted question", () => {
    const [first] = parseQuestionGroups(csv, "questions.csv");
    expect(first.queries[1]).toBe("Which tools, exactly, do they know?");
  });

  it("reads an escaped double quote", () => {
    const groups = parseQuestionGroups(
      `group_id,group_type,question_text\ng1,independent,"Who said ""yes""?"`,
      "q.csv",
    );
    expect(groups[0].queries[0]).toBe('Who said "yes"?');
  });

  it("throws when there is no question column", () => {
    expect(() =>
      parseQuestionGroups("group_id,group_type\ng1,independent", "q.csv"),
    ).toThrow(/question column/i);
  });

  it("throws on a header with no rows under it", () => {
    expect(() =>
      parseQuestionGroups("group_id,group_type,question_text", "q.csv"),
    ).toThrow(/no rows/i);
  });

  it("puts ungrouped rows in one group rather than one each", () => {
    const groups = parseQuestionGroups("question_text\nOne?\nTwo?", "flat.csv");
    expect(groups).toEqual([
      { type: "independent", queries: ["One?", "Two?"] },
    ]);
  });
});

describe("choosing the format", () => {
  it("reads JSON out of a file named .csv", () => {
    // Chosen by content, not only by extension — an export saved under the
    // wrong name still parses as what it is.
    const groups = parseQuestionGroups(
      JSON.stringify({
        query_groups: [{ type: "independent", queries: ["A?"] }],
      }),
      "questions.csv",
    );
    expect(groups[0].queries).toEqual(["A?"]);
  });

  it("throws on an empty file", () => {
    expect(() => parseQuestionGroups("   \n ", "q.csv")).toThrow(/empty/i);
  });
});
