import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  fmtQty,
  loadReceiptWidth,
  money,
  printConfigFor,
  printCss,
  saveReceiptWidth,
} from "@/features/pos/receiptFormat";

describe("printConfigFor", () => {
  it("borderless narrow ribbon for 58mm", () => {
    expect(printConfigFor("58")).toEqual({
      pageSize: "58mm auto",
      pageMargin: "0",
      contentWidth: "58mm",
    });
  });

  it("borderless 80mm ribbon", () => {
    expect(printConfigFor("80")).toEqual({
      pageSize: "80mm auto",
      pageMargin: "0",
      contentWidth: "80mm",
    });
  });

  it("A4 with margins", () => {
    const cfg = printConfigFor("A4");
    expect(cfg.pageSize).toBe("A4");
    expect(cfg.pageMargin).toBe("12mm");
  });
});

describe("printCss", () => {
  it("embeds the chosen @page size and hides the app while printing", () => {
    const css = printCss("80");
    expect(css).toContain("@page { size: 80mm auto; margin: 0; }");
    expect(css).toContain("body * { visibility: hidden");
    expect(css).toContain(".receipt-print");
  });
});

describe("money / fmtQty", () => {
  it("formats money to 2 decimals, tolerant of null", () => {
    expect(money("11")).toBe("11.00");
    expect(money("5.5")).toBe("5.50");
    expect(money(null)).toBe("0.00");
    expect(money(undefined)).toBe("0.00");
  });

  it("strips trailing zeros from qty", () => {
    expect(fmtQty("2.000")).toBe("2");
    expect(fmtQty("1.500")).toBe("1.5");
    expect(fmtQty("0.250")).toBe("0.25");
  });
});

describe("receipt width persistence", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => window.localStorage.clear());

  it("defaults to 80mm", () => {
    expect(loadReceiptWidth("r-1")).toBe("80");
  });

  it("round-trips per register", () => {
    saveReceiptWidth("r-1", "58");
    saveReceiptWidth("r-2", "A4");
    expect(loadReceiptWidth("r-1")).toBe("58");
    expect(loadReceiptWidth("r-2")).toBe("A4");
  });

  it("ignores an invalid stored value", () => {
    window.localStorage.setItem("pos:receiptWidth:r-1", "garbage");
    expect(loadReceiptWidth("r-1")).toBe("80");
  });
});
