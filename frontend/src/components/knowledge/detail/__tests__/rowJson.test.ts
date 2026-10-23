import { describe, expect, it } from "vitest";

import type { DocumentRow } from "@/components/knowledge/table/types";

import { rowJson } from "../rowJson";

describe("rowJson", () => {
  it("leads with the object id and leaves out fields added after processing", () => {
    const row = {
      objectId: "obj-1",
      values: { candidate_name: "Priya Sharma", skills: ["SQL"], added: null },
      notExtracted: new Set(["added"]),
    } as unknown as DocumentRow;

    expect(JSON.parse(rowJson(row))).toEqual({
      object_id: "obj-1",
      candidate_name: "Priya Sharma",
      skills: ["SQL"],
    });
    expect(Object.keys(JSON.parse(rowJson(row)))[0]).toBe("object_id");
  });
});
