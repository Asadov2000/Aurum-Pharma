// Theme preference: "light" | "dark" | "system".
// Stored in localStorage under theme:preference; applied as the [data-theme]
// attribute on <html>. A pre-paint inline script in index.html applies the
// initial value to avoid a flash; this module keeps it in sync at runtime.

export type ThemePreference = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

const STORAGE_KEY = "theme:preference";

function systemPrefersDark(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
  );
}

export function getThemePreference(): ThemePreference {
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    if (v === "light" || v === "dark" || v === "system") return v;
  } catch {
    // ignore — fall through to default
  }
  return "system";
}

export function resolveTheme(pref: ThemePreference): ResolvedTheme {
  if (pref === "system") return systemPrefersDark() ? "dark" : "light";
  return pref;
}

export function applyTheme(pref: ThemePreference): ResolvedTheme {
  const resolved = resolveTheme(pref);
  document.documentElement.setAttribute("data-theme", resolved);
  return resolved;
}

export function setThemePreference(pref: ThemePreference): ResolvedTheme {
  try {
    window.localStorage.setItem(STORAGE_KEY, pref);
  } catch {
    // ignore — preference just won't persist
  }
  return applyTheme(pref);
}

/**
 * Wire up live updates: re-apply when the OS theme changes while the user is
 * on "system". Returns an unsubscribe fn. Safe to call once at boot.
 */
export function watchSystemTheme(): () => void {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const onChange = () => {
    if (getThemePreference() === "system") applyTheme("system");
  };
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}
