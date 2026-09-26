import { afterEach, describe, expect, it, vi } from "vitest";

import { printCashReceipt } from "@/features/billing/cashReceiptPrint";
import { type CashReceipt } from "@/features/billing/cashTypes";

const RECEIPT: CashReceipt = {
  id: "receipt-1",
  document_number: "AC-2026-0042",
  tenant_id: "tenant-1",
  tenant_name: "<img src=x onerror=alert(1)>",
  purpose: "<script>document.body.remove()</script>",
  amount: "600.00",
  currency: "TJS",
  paid_at: "2026-09-26T08:00:00Z",
  confirmed_at: "2026-09-26T08:15:00Z",
  allocated_amount: "250.00",
  credit_amount: "350.00",
  allocations: [
    { invoice_id: "invoice-1", invoice_number: "<svg onload=alert(2)>", amount: "250.00" },
  ],
  title: "Подтверждение приёма наличных",
};

function mockFramePrinting(fail = false) {
  const descriptor = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, "contentWindow");
  const nativeGetter: (() => Window | null) | undefined = descriptor?.get;
  const print = vi.fn(() => {
    if (fail) throw new Error("print failed");
  });
  vi.spyOn(HTMLIFrameElement.prototype, "contentWindow", "get").mockImplementation(function (
    this: HTMLIFrameElement,
  ) {
    const printWindow = nativeGetter?.call(this) ?? null;
    if (printWindow) {
      vi.spyOn(printWindow, "focus").mockImplementation(() => undefined);
      vi.spyOn(printWindow, "print").mockImplementation(print);
    }
    return printWindow;
  });
  return print;
}

describe("cash receipt printing", () => {
  afterEach(() => {
    document.querySelectorAll("iframe").forEach((frame) => frame.remove());
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("prints in an isolated frame with customer values as text and no executable markup", async () => {
    vi.useFakeTimers();
    const print = mockFramePrinting();
    await printCashReceipt(RECEIPT);

    const frame = document.querySelector("iframe");
    expect(frame).not.toBeNull();
    expect(frame).toHaveAttribute("sandbox", "allow-same-origin allow-modals");
    const printDocument = frame?.contentDocument;
    expect(printDocument?.body.textContent).toContain(RECEIPT.tenant_name);
    expect(printDocument?.body.textContent).toContain(RECEIPT.purpose);
    expect(printDocument?.body.textContent).toContain(RECEIPT.allocations[0]?.invoice_number);
    expect(printDocument?.querySelector("script,img,svg")).toBeNull();
    expect(printDocument?.querySelectorAll("[onerror],[onload]")).toHaveLength(0);
    expect(print).toHaveBeenCalledTimes(1);
    frame?.contentWindow?.dispatchEvent(new Event("afterprint"));
    expect(window.document.querySelector("iframe")).toBeNull();
  });

  it("removes the print frame and rejects when the browser cannot print", async () => {
    vi.useFakeTimers();
    mockFramePrinting(true);
    await expect(printCashReceipt(RECEIPT)).rejects.toThrow("print failed");
    expect(document.querySelector("iframe")).toBeNull();
  });
});
