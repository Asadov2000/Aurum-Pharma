import { afterEach, describe, expect, it } from "vitest";

import { detectTouch, resolveMode } from "@/features/pos/usePosMode";

// jsdom exposes `ontouchstart` and leaves matchMedia undefined, so we drive the
// hardware signals explicitly. (matchMedia stays undefined → guarded in code.)
function setTouchHardware(opts: { ontouchstart: boolean; maxTouchPoints: number }): void {
  if (opts.ontouchstart) {
    Object.defineProperty(window, "ontouchstart", { value: null, configurable: true });
  } else {
    delete (window as unknown as { ontouchstart?: unknown }).ontouchstart;
  }
  Object.defineProperty(navigator, "maxTouchPoints", {
    value: opts.maxTouchPoints,
    configurable: true,
  });
}

afterEach(() => {
  // Restore jsdom defaults so other test files see a stable environment.
  Object.defineProperty(window, "ontouchstart", { value: null, configurable: true });
  Object.defineProperty(navigator, "maxTouchPoints", { value: undefined, configurable: true });
});

describe("resolveMode", () => {
  it("honours an explicit touch preference", () => {
    expect(resolveMode("touch")).toBe("touch");
  });

  it("honours an explicit keyboard preference", () => {
    expect(resolveMode("keyboard")).toBe("keyboard");
  });

  it("falls back to keyboard on non-touch hardware in auto", () => {
    setTouchHardware({ ontouchstart: false, maxTouchPoints: 0 });
    expect(resolveMode("auto")).toBe("keyboard");
  });

  it("resolves auto to touch when the device reports touch points", () => {
    setTouchHardware({ ontouchstart: false, maxTouchPoints: 5 });
    expect(resolveMode("auto")).toBe("touch");
  });
});

describe("detectTouch", () => {
  it("is false without any touch signal", () => {
    setTouchHardware({ ontouchstart: false, maxTouchPoints: 0 });
    expect(detectTouch()).toBe(false);
  });

  it("is true when maxTouchPoints > 0", () => {
    setTouchHardware({ ontouchstart: false, maxTouchPoints: 3 });
    expect(detectTouch()).toBe(true);
  });

  it("is true when ontouchstart is present", () => {
    setTouchHardware({ ontouchstart: true, maxTouchPoints: 0 });
    expect(detectTouch()).toBe(true);
  });
});
