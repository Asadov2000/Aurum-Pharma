import { type ReceiptWidth } from "./types";

/**
 * Receipt width templates. 58/80 mm are thermal ribbon (borderless, narrow);
 * A4 is a full page with margins. The browser does the actual printing via
 * window.print(); the chosen width drives the @page size + the on-screen
 * preview width. Pure helpers here so the geometry is unit-testable.
 */

export const RECEIPT_WIDTHS: ReceiptWidth[] = ["58", "80", "A4"];

export const receiptWidthLabel: Record<ReceiptWidth, string> = {
  "58": "58 мм (лента)",
  "80": "80 мм (лента)",
  A4: "A4 (лист)",
};

const WIDTH_KEY = (registerId: string): string => `pos:receiptWidth:${registerId}`;

export function loadReceiptWidth(registerId: string): ReceiptWidth {
  try {
    const v = window.localStorage.getItem(WIDTH_KEY(registerId));
    if (v === "58" || v === "80" || v === "A4") return v;
  } catch {
    // ignore
  }
  return "80";
}

export function saveReceiptWidth(registerId: string, width: ReceiptWidth): void {
  try {
    window.localStorage.setItem(WIDTH_KEY(registerId), width);
  } catch {
    // ignore
  }
}

export interface PrintConfig {
  /** CSS @page `size` value. */
  pageSize: string;
  /** CSS @page `margin` value. */
  pageMargin: string;
  /** On-screen + print content width (CSS length). */
  contentWidth: string;
}

export function printConfigFor(width: ReceiptWidth): PrintConfig {
  switch (width) {
    case "58":
      return { pageSize: "58mm auto", pageMargin: "0", contentWidth: "58mm" };
    case "80":
      return { pageSize: "80mm auto", pageMargin: "0", contentWidth: "80mm" };
    case "A4":
      return { pageSize: "A4", pageMargin: "12mm", contentWidth: "186mm" };
  }
}

/**
 * The print stylesheet: hide the whole app, show only the receipt subtree at
 * the top-left, and set the page geometry for the chosen width. Returned as a
 * string so it can be dropped into a <style> tag that React keeps in sync.
 */
export function printCss(width: ReceiptWidth): string {
  const cfg = printConfigFor(width);
  return [
    "@media print {",
    "  body * { visibility: hidden !important; }",
    "  .receipt-print, .receipt-print * { visibility: visible !important; }",
    `  .receipt-print { position: absolute; left: 0; top: 0; width: ${cfg.contentWidth}; }`,
    "  .receipt-no-print { display: none !important; }",
    `  @page { size: ${cfg.pageSize}; margin: ${cfg.pageMargin}; }`,
    "}",
  ].join("\n");
}

/** Format a numeric string to 2 decimals, tolerant of nulls. */
export function money(value: string | null | undefined): string {
  const n = Number(value ?? 0);
  return Number.isFinite(n) ? n.toFixed(2) : "0.00";
}

/** Drop trailing zeros from a qty like "2.000" → "2", "1.500" → "1.5". */
export function fmtQty(value: string): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  return String(Number(n.toFixed(3)));
}
