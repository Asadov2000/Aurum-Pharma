import { beforeEach, describe, expect, it } from "vitest";

import {
  applyDensity,
  DENSITY_STORAGE_KEY,
  getDensityPreference,
  setDensityPreference,
} from "@/lib/density";

describe("interface density", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-density");
  });

  it("uses the comfortable mode for missing or invalid preferences", () => {
    expect(getDensityPreference()).toBe("comfortable");

    window.localStorage.setItem(DENSITY_STORAGE_KEY, "oversized");
    expect(getDensityPreference()).toBe("comfortable");
  });

  it("persists only the selected visual mode and applies it immediately", () => {
    setDensityPreference("touch");

    expect(window.localStorage.getItem(DENSITY_STORAGE_KEY)).toBe("touch");
    expect(document.documentElement).toHaveAttribute("data-density", "touch");
  });

  it("can apply a mode without writing storage", () => {
    applyDensity("compact");

    expect(window.localStorage.getItem(DENSITY_STORAGE_KEY)).toBeNull();
    expect(document.documentElement).toHaveAttribute("data-density", "compact");
  });
});
