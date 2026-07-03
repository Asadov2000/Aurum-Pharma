import {
  getDesktopBridge,
  getDesktopBridgeInfo,
  hasDesktopBridge,
  hasDesktopCapability,
  postDesktopMessage,
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
});

function createTarget(target: DesktopBridgeTarget = {}): DesktopBridgeTarget {
  return target;
}
