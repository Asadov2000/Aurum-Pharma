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

  it("recognises one physical scanner burst while product search has focus", () => {
    const onScan = vi.fn();
    const searchKeyDown = vi.fn();
    render(
      <>
        <BarcodeListener enabled onScan={onScan} />
        <input id="pos-product-search" aria-label="Товар" onKeyDown={searchKeyDown} />
      </>,
    );
    const search = document.getElementById("pos-product-search");
    search?.focus();

    for (const key of "4600123456789") {
      fireEvent.keyDown(search as HTMLInputElement, { key });
    }
    const propagated = fireEvent.keyDown(search as HTMLInputElement, { key: "Enter" });

    expect(propagated).toBe(false);
    expect(onScan).toHaveBeenCalledTimes(1);
    expect(onScan).toHaveBeenCalledWith("4600123456789");
    expect(searchKeyDown).not.toHaveBeenCalledWith(expect.objectContaining({ key: "Enter" }));
  });

  it("leaves ordinary manual search input and Enter to the product picker", () => {
    const onScan = vi.fn();
    const searchKeyDown = vi.fn();
    render(
      <>
        <BarcodeListener enabled onScan={onScan} />
        <input id="pos-product-search" aria-label="Товар" onKeyDown={searchKeyDown} />
      </>,
    );
    const search = document.getElementById("pos-product-search") as HTMLInputElement;
    search.focus();
    let timestamp = 0;
    const now = vi.spyOn(performance, "now").mockImplementation(() => {
      timestamp += 200;
      return timestamp;
    });

    for (const key of "Аспи") {
      fireEvent.keyDown(search, { key });
    }
    const propagated = fireEvent.keyDown(search, { key: "Enter" });
    now.mockRestore();

    expect(propagated).toBe(true);
    expect(onScan).not.toHaveBeenCalled();
    expect(searchKeyDown).toHaveBeenCalledTimes(5);
  });
});
