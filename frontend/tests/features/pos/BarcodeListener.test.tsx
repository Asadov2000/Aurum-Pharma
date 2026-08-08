import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { dispatchDesktopBarcodeScan } from "@/lib/desktopBridge";
import { BarcodeListener } from "@/features/pos/BarcodeListener";

describe("BarcodeListener", () => {
  it("forwards desktop barcode scan events while enabled", () => {
    const onScan = vi.fn();
    render(<BarcodeListener enabled onScan={onScan} />);

    expect(dispatchDesktopBarcodeScan(" 4600123456789 ")).toBe(true);

    expect(onScan).toHaveBeenCalledWith("4600123456789");
  });

  it("ignores desktop barcode scan events while disabled", () => {
    const onScan = vi.fn();
    render(<BarcodeListener enabled={false} onScan={onScan} />);

    expect(dispatchDesktopBarcodeScan("4600123456789")).toBe(true);

    expect(onScan).not.toHaveBeenCalled();
  });

  it("reserves the Enter key that terminates a physical scanner burst", () => {
    const onScan = vi.fn();
    render(<BarcodeListener enabled onScan={onScan} />);

    for (const key of "4600123456789") {
      fireEvent.keyDown(window, { key });
    }
    const propagated = fireEvent.keyDown(window, { key: "Enter" });

    expect(propagated).toBe(false);
    expect(onScan).toHaveBeenCalledWith("4600123456789");
  });

  it("reserves Enter after a malformed short scanner burst without starting a payment", () => {
    const onScan = vi.fn();
    render(<BarcodeListener enabled onScan={onScan} />);

    fireEvent.keyDown(window, { key: "1" });
    fireEvent.keyDown(window, { key: "2" });
    const propagated = fireEvent.keyDown(window, { key: "Enter" });

    expect(propagated).toBe(false);
    expect(onScan).not.toHaveBeenCalled();
  });
});
