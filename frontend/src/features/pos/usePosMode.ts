import { useCallback, useState } from "react";

export type PosMode = "touch" | "keyboard";
export type PosModePref = "auto" | "touch" | "keyboard";

const STORAGE_KEY = "pos:mode";

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

function readPref(): PosModePref {
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    if (v === "auto" || v === "touch" || v === "keyboard") return v;
  } catch {
    // ignore
  }
  return "auto";
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
  const [pref, setPrefState] = useState<PosModePref>(() => readPref());

  const setPref = useCallback((p: PosModePref) => {
    try {
      window.localStorage.setItem(STORAGE_KEY, p);
    } catch {
      // ignore — just won't persist
    }
    setPrefState(p);
  }, []);

  return { mode: resolveMode(pref), pref, setPref };
}
