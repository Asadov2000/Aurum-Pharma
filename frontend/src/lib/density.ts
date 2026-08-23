export type UiDensity = "compact" | "comfortable" | "touch";
export type DensityPreference = "auto" | UiDensity;

export const DENSITY_STORAGE_KEY = "ui:density";
const DEFAULT_DENSITY: UiDensity = "comfortable";

function isDensityPreference(value: string | null): value is DensityPreference {
  return value === "auto" || value === "compact" || value === "comfortable" || value === "touch";
}

export function getDensityPreference(): DensityPreference {
  try {
    const stored = window.localStorage.getItem(DENSITY_STORAGE_KEY);
    return isDensityPreference(stored) ? stored : DEFAULT_DENSITY;
  } catch {
    return DEFAULT_DENSITY;
  }
}

export function resolveDensity(density: DensityPreference): UiDensity {
  if (density !== "auto") return density;
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return DEFAULT_DENSITY;
  }
  return window.matchMedia("(pointer: coarse)").matches ? "touch" : DEFAULT_DENSITY;
}

export function applyDensity(density: DensityPreference): void {
  document.documentElement.setAttribute("data-density", resolveDensity(density));
}

export function setDensityPreference(density: DensityPreference): void {
  try {
    window.localStorage.setItem(DENSITY_STORAGE_KEY, density);
  } catch {
    // The visual preference still applies for the current session.
  }
  applyDensity(density);
}
