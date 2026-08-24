import { setDensityPreference, type DensityPreference } from "@/lib/density";
import { setThemePreference } from "@/lib/theme";

import { type AccentPreference, type ContrastPreference, type UserPreferences } from "./types";

export const APPEARANCE_CHANGED_EVENT = "aurum:appearance-changed";

const CONTRAST_KEY = "ui:contrast";
const REDUCE_MOTION_KEY = "ui:reduce-motion";
const ACCENT_KEY = "ui:accent";

export interface LocalAppearance {
  contrast: ContrastPreference;
  reduceMotion: boolean;
  accent: AccentPreference;
}

export function getLocalAppearance(): LocalAppearance {
  try {
    const contrast = window.localStorage.getItem(CONTRAST_KEY);
    const accent = window.localStorage.getItem(ACCENT_KEY);
    return {
      contrast: contrast === "high" ? "high" : "standard",
      reduceMotion: window.localStorage.getItem(REDUCE_MOTION_KEY) === "1",
      accent: isAccent(accent) ? accent : "teal",
    };
  } catch {
    return { contrast: "standard", reduceMotion: false, accent: "teal" };
  }
}

export function applyLocalAppearance(value: LocalAppearance): void {
  document.documentElement.setAttribute("data-contrast", value.contrast);
  document.documentElement.setAttribute(
    "data-reduce-motion",
    value.reduceMotion ? "true" : "false",
  );
  document.documentElement.setAttribute("data-accent", value.accent);
  try {
    window.localStorage.setItem(CONTRAST_KEY, value.contrast);
    window.localStorage.setItem(REDUCE_MOTION_KEY, value.reduceMotion ? "1" : "0");
    window.localStorage.setItem(ACCENT_KEY, value.accent);
  } catch {
    // Preferences still apply for the current browser session.
  }
  window.dispatchEvent(new CustomEvent(APPEARANCE_CHANGED_EVENT));
}

export function applyUserPreferences(preferences: UserPreferences): void {
  setThemePreference(preferences.theme);
  setDensityPreference(preferences.density as DensityPreference);
  applyLocalAppearance({
    contrast: preferences.contrast,
    reduceMotion: preferences.reduce_motion,
    accent: preferences.accent,
  });
}

function isAccent(value: string | null): value is AccentPreference {
  return (
    value === "teal" ||
    value === "blue" ||
    value === "violet" ||
    value === "green" ||
    value === "amber" ||
    value === "rose"
  );
}
