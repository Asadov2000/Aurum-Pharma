import { useDevicePreferences, type DevicePosMode } from "@/lib/devicePreferences";

export type PosMode = "touch" | "keyboard";
export type PosModePref = DevicePosMode;

/** Hardware sniff used when the preference is "auto". */
export function detectTouch(): boolean {
  if (typeof window === "undefined") return false;
  if ("ontouchstart" in window) return true;
  if (typeof navigator !== "undefined" && navigator.maxTouchPoints > 0) return true;
  // matchMedia is missing in some test/SSR environments — guard it.
  if (typeof window.matchMedia === "function") {
    return window.matchMedia("(pointer: coarse)").matches;
  }
  return false;
}

export function resolveMode(pref: PosModePref): PosMode {
  if (pref === "touch") return "touch";
  if (pref === "keyboard") return "keyboard";
  return detectTouch() ? "touch" : "keyboard";
}

/**
 * POS interaction mode. The preference (auto/touch/keyboard) persists in
 * localStorage as a per-device override; "auto" sniffs the hardware. A future
 * branch-settings field can seed the default server-side — until then the
 * device override is the source of truth.
 */
export function usePosMode(): {
  mode: PosMode;
  pref: PosModePref;
  setPref: (p: PosModePref) => void;
} {
  const { preferences, updatePreferences } = useDevicePreferences();
  const pref = preferences.posMode;
  const setPref = (next: PosModePref) => updatePreferences({ posMode: next });

  return { mode: resolveMode(pref), pref, setPref };
}
