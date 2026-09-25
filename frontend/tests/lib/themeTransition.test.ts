import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setThemePreferenceWithTransition } from "@/lib/themeTransition";

describe("theme transition", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.setAttribute("data-theme", "light");
    document.documentElement.setAttribute("data-reduce-motion", "false");
    document.documentElement.classList.remove(
      "theme-slant-transition",
      "theme-slant-from-left",
      "theme-slant-from-right",
    );
    Object.defineProperty(document, "startViewTransition", {
      configurable: true,
      value: undefined,
    });
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi
        .fn()
        .mockImplementation((query: string) => ({ matches: query === "(pointer: fine)" })),
    });
  });

  afterEach(() => vi.restoreAllMocks());

  it("applies immediately when the browser cannot animate", () => {
    expect(setThemePreferenceWithTransition("dark")).toBe("dark");
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(window.localStorage.getItem("theme:preference")).toBe("dark");
    expect(document.documentElement).not.toHaveClass("theme-slant-transition");
  });

  it("respects the in-app reduced-motion preference", () => {
    const start = vi.fn();
    Object.defineProperty(document, "startViewTransition", { configurable: true, value: start });
    document.documentElement.setAttribute("data-reduce-motion", "true");

    setThemePreferenceWithTransition("dark");

    expect(start).not.toHaveBeenCalled();
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
  });

  it("changes immediately without a precise pointer", () => {
    const start = vi.fn();
    Object.defineProperty(document, "startViewTransition", { configurable: true, value: start });
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockImplementation(() => ({ matches: false })),
    });

    setThemePreferenceWithTransition("dark");

    expect(start).not.toHaveBeenCalled();
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
  });

  it("keeps the slanted reveal while the pointer moves and cleans up after completion", async () => {
    let finish!: () => void;
    const finished = new Promise<void>((resolve) => {
      finish = resolve;
    });
    const start = vi.fn((update: () => void) => {
      update();
      return { ready: Promise.resolve(), finished, skipTransition: vi.fn() };
    });
    Object.defineProperty(document, "startViewTransition", { configurable: true, value: start });
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      frames.push(callback);
      return frames.length;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => undefined);
    window.dispatchEvent(new MouseEvent("pointermove", { clientX: 30, clientY: 100 }));

    setThemePreferenceWithTransition("dark");
    await Promise.resolve();

    const root = document.documentElement;
    expect(start).toHaveBeenCalledOnce();
    expect(root).toHaveClass("theme-slant-transition", "theme-slant-from-left");
    const initialTop = Number.parseFloat(root.style.getPropertyValue("--theme-slant-top"));
    window.dispatchEvent(new MouseEvent("pointermove", { clientX: 400, clientY: 100 }));
    frames.shift()?.(performance.now() + 100);
    const movedTop = Number.parseFloat(root.style.getPropertyValue("--theme-slant-top"));
    expect(movedTop).toBeGreaterThan(initialTop);

    finish();
    await finished;
    await Promise.resolve();
    expect(root).not.toHaveClass("theme-slant-transition");
    expect(root.style.getPropertyValue("--theme-slant-top")).toBe("");
    expect(root).toHaveAttribute("data-theme", "dark");
  });

  it("does not let an earlier transition clear a newer one", async () => {
    const finishes: Array<() => void> = [];
    const start = vi.fn((update: () => void) => {
      update();
      const finished = new Promise<void>((resolve) => finishes.push(resolve));
      return { ready: Promise.resolve(), finished, skipTransition: vi.fn() };
    });
    Object.defineProperty(document, "startViewTransition", { configurable: true, value: start });
    window.dispatchEvent(new MouseEvent("pointermove", { clientX: 30, clientY: 100 }));

    setThemePreferenceWithTransition("dark");
    setThemePreferenceWithTransition("light");
    finishes[0]();
    await Promise.resolve();
    await Promise.resolve();
    expect(document.documentElement).toHaveClass("theme-slant-transition");
    finishes[1]();
    await Promise.resolve();
    await Promise.resolve();
    expect(document.documentElement).not.toHaveClass("theme-slant-transition");
  });
});
