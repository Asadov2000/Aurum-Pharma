export type UiDensity = "compact" | "comfortable" | "touch";

export const DENSITY_STORAGE_KEY = "ui:density";
const DEFAULT_DENSITY: UiDensity = "comfortable";

function isUiDensity(value: string | null): value is UiDensity {
  return value === "compact" || value === "comfortable" || value === "touch";
}

export function getDensityPreference(): UiDensity {
  try {
    const stored = window.localStorage.getItem(DENSITY_STORAGE_KEY);
    return isUiDensity(stored) ? stored : DEFAULT_DENSITY;
  } catch {
    return DEFAULT_DENSITY;
  }
}

export function applyDensity(density: UiDensity): void {
  document.documentElement.setAttribute("data-density", density);
}

export function setDensityPreference(density: UiDensity): void {
  try {
    window.localStorage.setItem(DENSITY_STORAGE_KEY, density);
  } catch {
    // The visual preference still applies for the current session.
  }
  applyDensity(density);
}
