import {
  parseQuickActionMenuPreferences,
  type QuickActionMenuPreferences,
  type QuickActionSection,
} from "./quickActions";

export const QUICK_ACTIONS_CHANGED_EVENT = "aurum:quick-actions-changed";
const PREFIX = "aurum:quick-actions:v1";

export function loadQuickActionPreferences(
  scope: string,
  section: QuickActionSection,
): QuickActionMenuPreferences | null {
  try {
    const raw = window.localStorage.getItem(storageKey(scope, section));
    return raw === null ? null : parseQuickActionMenuPreferences(JSON.parse(raw), section);
  } catch {
    return null;
  }
}

export function saveQuickActionPreferences(
  scope: string,
  section: QuickActionSection,
  value: QuickActionMenuPreferences,
): void {
  const config = parseQuickActionMenuPreferences(value, section);
  if (!config) return;
  try {
    const key = storageKey(scope, section);
    const serialized = JSON.stringify(config);
    if (window.localStorage.getItem(key) === serialized) return;
    window.localStorage.setItem(key, serialized);
  } catch {
    // The pending preference queue still retains the saved configuration in memory.
  }
  window.dispatchEvent(
    new CustomEvent(QUICK_ACTIONS_CHANGED_EVENT, { detail: { scope, section } }),
  );
}

function storageKey(scope: string, section: QuickActionSection): string {
  return `${PREFIX}:${encodeURIComponent(scope)}:${section}`;
}
