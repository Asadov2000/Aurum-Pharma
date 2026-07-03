import {
  DESKTOP_BARCODE_MAX_LENGTH,
  DESKTOP_BARCODE_SCANNED_EVENT,
  DESKTOP_FILE_NAME_MAX_LENGTH,
  dispatchDesktopBarcodeScan,
  getDesktopBridge,
  getDesktopBridgeInfo,
  hasDesktopBridge,
  hasDesktopCapability,
  notifyDesktopReady,
  normalizeDesktopBarcode,
  normalizeDesktopFileName,
  postDesktopMessage,
  requestDesktopCashDrawerOpen,
  requestDesktopFileExport,
  requestDesktopReceiptPrint,
  type DesktopBridgeTarget,
} from "@/lib/desktopBridge";
import { describe, expect, it, vi } from "vitest";

describe("desktopBridge", () => {
  it("reports no bridge in a regular browser", () => {
    const target = createTarget();

    expect(hasDesktopBridge(target)).toBe(false);
    expect(getDesktopBridge(target)).toBeNull();
    expect(getDesktopBridgeInfo(target)).toEqual({
      appVersion: null,
      capabilities: [],
      isAvailable: false,
      platform: null,
    });
  });

  it("reads the app-owned Windows bridge metadata", () => {
    const target = createTarget({
      aurumDesktop: {
        appVersion: " 1.2.3 ",
        capabilities: ["receipt-print", "receipt-print", "file-export"],
        platform: "windows",
      },
    });

    expect(hasDesktopBridge(target)).toBe(true);
    expect(getDesktopBridgeInfo(target)).toEqual({
      appVersion: "1.2.3",
      capabilities: ["file-export", "receipt-print"],
      isAvailable: true,
      platform: "windows",
    });
    expect(hasDesktopCapability("receipt-print", target)).toBe(true);
    expect(hasDesktopCapability("cash-drawer", target)).toBe(false);
  });

  it("detects a raw WebView2 bridge even before aurumDesktop is injected", () => {
    const target = createTarget({
      chrome: {
        webview: {},
      },
    });

    expect(hasDesktopBridge(target)).toBe(true);
    expect(getDesktopBridgeInfo(target)).toEqual({
      appVersion: null,
      capabilities: [],
      isAvailable: true,
      platform: "windows",
    });
  });

  it("posts messages through aurumDesktop first", () => {
    const postMessage = vi.fn();
    const webviewPostMessage = vi.fn();
    const target = createTarget({
      aurumDesktop: {
        postMessage,
      },
      chrome: {
        webview: {
          postMessage: webviewPostMessage,
        },
      },
    });

    const sent = postDesktopMessage(
      {
        payload: { saleId: "sale-1" },
        type: "aurum.receipt.print",
      },
      target,
    );

    expect(sent).toBe(true);
    expect(postMessage).toHaveBeenCalledWith({
      payload: { saleId: "sale-1" },
      type: "aurum.receipt.print",
    });
    expect(webviewPostMessage).not.toHaveBeenCalled();
  });

  it("falls back to the WebView2 postMessage bridge", () => {
    const postMessage = vi.fn();
    const target = createTarget({
      chrome: {
        webview: {
          postMessage,
        },
      },
    });

    expect(postDesktopMessage({ type: "aurum.desktop.ready" }, target)).toBe(true);
    expect(postMessage).toHaveBeenCalledWith({ type: "aurum.desktop.ready" });
  });

  it("notifies the desktop host when the web app is ready", () => {
    const postMessage = vi.fn();
    const target = createTarget({
      aurumDesktop: {
        postMessage,
      },
    });

    expect(notifyDesktopReady(target)).toBe(true);
    expect(postMessage).toHaveBeenCalledWith({ type: "aurum.desktop.ready" });
  });

  it("requests native receipt printing with a normalized sale id", () => {
    const postMessage = vi.fn();
    const target = createTarget({
      aurumDesktop: {
        capabilities: ["receipt-print"],
        postMessage,
      },
    });

    expect(requestDesktopReceiptPrint(" sale-1 ", target)).toBe(true);
    expect(postMessage).toHaveBeenCalledWith({
      payload: { saleId: "sale-1" },
      type: "aurum.receipt.print",
    });
  });

  it("does not request receipt printing with an empty sale id", () => {
    const postMessage = vi.fn();
    const target = createTarget({
      aurumDesktop: {
        capabilities: ["receipt-print"],
        postMessage,
      },
    });

    expect(requestDesktopReceiptPrint("   ", target)).toBe(false);
    expect(postMessage).not.toHaveBeenCalled();
  });

  it("does not request receipt printing when the app-owned bridge lacks the capability", () => {
    const postMessage = vi.fn();
    const target = createTarget({
      aurumDesktop: {
        capabilities: ["file-export"],
        postMessage,
      },
    });

    expect(requestDesktopReceiptPrint("sale-1", target)).toBe(false);
    expect(postMessage).not.toHaveBeenCalled();
  });

  it("allows receipt printing through a raw WebView2 bridge without capability metadata", () => {
    const postMessage = vi.fn();
    const target = createTarget({
      chrome: {
        webview: {
          postMessage,
        },
      },
    });

    expect(requestDesktopReceiptPrint("sale-1", target)).toBe(true);
    expect(postMessage).toHaveBeenCalledWith({
      payload: { saleId: "sale-1" },
      type: "aurum.receipt.print",
    });
  });

  it("requests opening the cash drawer with normalized context", () => {
    const postMessage = vi.fn();
    const target = createTarget({
      aurumDesktop: {
        capabilities: ["cash-drawer"],
        postMessage,
      },
    });

    expect(
      requestDesktopCashDrawerOpen(
        {
          reason: "sale-completed",
          registerId: " register-1 ",
          saleId: " sale-1 ",
        },
        target,
      ),
    ).toBe(true);
    expect(postMessage).toHaveBeenCalledWith({
      payload: {
        reason: "sale-completed",
        registerId: "register-1",
        saleId: "sale-1",
      },
      type: "aurum.cash-drawer.open",
    });
  });

  it("opens the cash drawer manually without blank optional ids", () => {
    const postMessage = vi.fn();
    const target = createTarget({
      aurumDesktop: {
        capabilities: ["cash-drawer"],
        postMessage,
      },
    });

    expect(
      requestDesktopCashDrawerOpen(
        {
          registerId: "   ",
          saleId: "",
        },
        target,
      ),
    ).toBe(true);
    expect(postMessage).toHaveBeenCalledWith({
      payload: {
        reason: "manual",
      },
      type: "aurum.cash-drawer.open",
    });
  });

  it("does not request opening the cash drawer when capability is absent", () => {
    const postMessage = vi.fn();
    const target = createTarget({
      aurumDesktop: {
        capabilities: ["receipt-print"],
        postMessage,
      },
    });

    expect(requestDesktopCashDrawerOpen({ reason: "manual" }, target)).toBe(false);
    expect(postMessage).not.toHaveBeenCalled();
  });

  it("allows opening the cash drawer through a raw WebView2 bridge", () => {
    const postMessage = vi.fn();
    const target = createTarget({
      chrome: {
        webview: {
          postMessage,
        },
      },
    });

    expect(requestDesktopCashDrawerOpen({ reason: "manual" }, target)).toBe(true);
    expect(postMessage).toHaveBeenCalledWith({
      payload: {
        reason: "manual",
      },
      type: "aurum.cash-drawer.open",
    });
  });

  it("requests native file export with safe metadata", () => {
    const postMessage = vi.fn();
    const target = createTarget({
      aurumDesktop: {
        capabilities: ["file-export"],
        postMessage,
      },
    });

    expect(
      requestDesktopFileExport(
        {
          fileName: " sales<summary>.xlsx ",
          mimeType:
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          sizeBytes: 42,
        },
        target,
      ),
    ).toBe(true);
    expect(postMessage).toHaveBeenCalledWith({
      payload: {
        fileName: "sales_summary_.xlsx",
        mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        sizeBytes: 42,
      },
      type: "aurum.file-export.request",
    });
  });

  it("omits invalid optional file export metadata", () => {
    const postMessage = vi.fn();
    const target = createTarget({
      aurumDesktop: {
        capabilities: ["file-export"],
        postMessage,
      },
    });

    expect(
      requestDesktopFileExport(
        {
          fileName: "report.xlsx",
          mimeType: "not-a-mime-type",
          sizeBytes: -1,
        },
        target,
      ),
    ).toBe(true);
    expect(postMessage).toHaveBeenCalledWith({
      payload: {
        fileName: "report.xlsx",
      },
      type: "aurum.file-export.request",
    });
  });

  it("does not request native file export when capability is absent", () => {
    const postMessage = vi.fn();
    const target = createTarget({
      aurumDesktop: {
        capabilities: ["receipt-print"],
        postMessage,
      },
    });

    expect(requestDesktopFileExport({ fileName: "report.xlsx" }, target)).toBe(false);
    expect(postMessage).not.toHaveBeenCalled();
  });

  it("allows native file export through a raw WebView2 bridge", () => {
    const postMessage = vi.fn();
    const target = createTarget({
      chrome: {
        webview: {
          postMessage,
        },
      },
    });

    expect(requestDesktopFileExport({ fileName: "report.xlsx" }, target)).toBe(true);
    expect(postMessage).toHaveBeenCalledWith({
      payload: {
        fileName: "report.xlsx",
      },
      type: "aurum.file-export.request",
    });
  });

  it("returns false when no desktop bridge can receive a message", () => {
    expect(postDesktopMessage({ type: "aurum.desktop.ready" }, createTarget())).toBe(
      false,
    );
  });

  it("returns false when the native bridge rejects a message", () => {
    const target = createTarget({
      aurumDesktop: {
        postMessage: () => {
          throw new Error("native bridge failed");
        },
      },
    });

    expect(postDesktopMessage({ type: "aurum.desktop.ready" }, target)).toBe(false);
  });

  it("normalizes desktop barcode values", () => {
    expect(normalizeDesktopBarcode(" 4600123456789 ")).toBe("4600123456789");
    expect(normalizeDesktopBarcode("   ")).toBeNull();
    expect(normalizeDesktopBarcode("1".repeat(DESKTOP_BARCODE_MAX_LENGTH + 1))).toBeNull();
  });

  it("normalizes desktop export file names", () => {
    expect(normalizeDesktopFileName(" report<>:\"/\\|?*.xlsx ")).toBe(
      "report_________.xlsx",
    );
    expect(normalizeDesktopFileName("CON")).toBeNull();
    expect(normalizeDesktopFileName("   ")).toBeNull();
    expect(
      normalizeDesktopFileName("x".repeat(DESKTOP_FILE_NAME_MAX_LENGTH + 1)),
    ).toBeNull();
  });

  it("dispatches normalized desktop barcode scans", () => {
    const listener = vi.fn();
    window.addEventListener(DESKTOP_BARCODE_SCANNED_EVENT, listener);

    try {
      expect(dispatchDesktopBarcodeScan(" 4600123456789 ")).toBe(true);
      expect(listener).toHaveBeenCalledTimes(1);
      expect(listener.mock.calls[0]?.[0]).toMatchObject({
        detail: { code: "4600123456789" },
      });
    } finally {
      window.removeEventListener(DESKTOP_BARCODE_SCANNED_EVENT, listener);
    }
  });

  it("does not dispatch empty desktop barcode scans", () => {
    const listener = vi.fn();
    window.addEventListener(DESKTOP_BARCODE_SCANNED_EVENT, listener);

    try {
      expect(dispatchDesktopBarcodeScan("   ")).toBe(false);
      expect(listener).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener(DESKTOP_BARCODE_SCANNED_EVENT, listener);
    }
  });
});

function createTarget(target: DesktopBridgeTarget = {}): DesktopBridgeTarget {
  return target;
}
