import { render } from "@testing-library/react";
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
});
