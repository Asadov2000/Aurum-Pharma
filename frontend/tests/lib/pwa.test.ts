import { registerPwaServiceWorker } from "@/lib/pwa";
import { describe, expect, it, vi } from "vitest";

describe("registerPwaServiceWorker", () => {
  it("skips registration in the Vite test environment", () => {
    const originalDescriptor = Object.getOwnPropertyDescriptor(
      window.navigator,
      "serviceWorker",
    );
    const serviceWorker = { register: vi.fn() };
    Object.defineProperty(window.navigator, "serviceWorker", {
      configurable: true,
      value: serviceWorker,
    });
    const addEventListener = vi.spyOn(window, "addEventListener");

    try {
      registerPwaServiceWorker();

      expect(addEventListener).not.toHaveBeenCalledWith(
        "load",
        expect.any(Function),
      );
      expect(serviceWorker.register).not.toHaveBeenCalled();
    } finally {
      addEventListener.mockRestore();
      if (originalDescriptor) {
        Object.defineProperty(window.navigator, "serviceWorker", originalDescriptor);
      } else {
        Reflect.deleteProperty(window.navigator, "serviceWorker");
      }
    }
  });
});
