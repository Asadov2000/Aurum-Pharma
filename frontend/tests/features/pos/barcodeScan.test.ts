// @vitest-environment node

import { describe, expect, it } from "vitest";

import { emptyScanBuffer, feedScanKey } from "@/features/pos/barcodeScan";

/** Feed a sequence of [key, timestamp] events; return the last recognised code. */
function feed(events: Array<[string, number]>): string | null {
  let buf = emptyScanBuffer();
  let code: string | null = null;
  for (const [key, now] of events) {
    const r = feedScanKey(buf, key, now);
    buf = r.buf;
    code = r.code;
  }
  return code;
}

describe("feedScanKey", () => {
  it("recognises a fast digit burst ending in Enter", () => {
    const code = feed([
      ["4", 0],
      ["6", 10],
      ["0", 20],
      ["0", 30],
      ["1", 40],
      ["Enter", 50],
    ]);
    expect(code).toBe("46001");
  });

  it("ignores slow human typing", () => {
    const code = feed([
      ["4", 0],
      ["6", 200],
      ["0", 400],
      ["0", 600],
      ["1", 800],
      ["Enter", 1000],
    ]);
    expect(code).toBeNull();
  });

  it("ignores a burst shorter than the minimum length", () => {
    expect(feed([["1", 0], ["2", 10], ["Enter", 20]])).toBeNull();
  });

  it("returns null on Enter with an empty buffer", () => {
    expect(feedScanKey(emptyScanBuffer(), "Enter", 0).code).toBeNull();
  });

  it("keeps only the tail after a slow gap so a split burst doesn't scan", () => {
    // 500ms gap resets the buffer; the fast tail alone is too short.
    expect(
      feed([
        ["9", 0],
        ["9", 500],
        ["9", 510],
        ["Enter", 520],
      ]),
    ).toBeNull();
  });

  it("ignores non-character keys mid-burst", () => {
    const code = feed([
      ["7", 0],
      ["Shift", 5],
      ["8", 10],
      ["9", 20],
      ["0", 30],
      ["Enter", 40],
    ]);
    expect(code).toBe("7890");
  });
});
