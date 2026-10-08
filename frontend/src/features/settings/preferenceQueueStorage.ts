import { type UserPreferencesUpdate, type WorkspacePreferences } from "./types";
import { parseWorkspace } from "./workspacePreferences";

export type PreferencePatch = Omit<UserPreferencesUpdate, "expected_version">;
export const PREFERENCE_QUEUE_CHANGED_EVENT = "aurum:preferences-pending-changed";

export function preferenceQueueKey(scope: string, channel: string): string {
  return `aurum:preferences-pending:v1:${encodeURIComponent(scope)}:${encodeURIComponent(channel)}`;
}

export function persistPreferencePatch(
  scope: string,
  channel: string,
  patch: PreferencePatch,
): void {
  try {
    const key = preferenceQueueKey(scope, channel);
    if (Object.keys(patch).length === 0) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, JSON.stringify(patch));
  } catch {
    // The owner retains the pending patch in memory when storage is unavailable.
  }
  window.dispatchEvent(
    new CustomEvent(PREFERENCE_QUEUE_CHANGED_EVENT, { detail: { scope, channel } }),
  );
}

export function readPendingWorkspace(scope: string, channel: string): WorkspacePreferences | null {
  try {
    const value: unknown = JSON.parse(
      window.localStorage.getItem(preferenceQueueKey(scope, channel)) ?? "null",
    );
    if (
      typeof value !== "object" ||
      value === null ||
      Array.isArray(value) ||
      Object.keys(value).length !== 1 ||
      !("workspace" in value)
    )
      return null;
    return parseWorkspace(value.workspace);
  } catch {
    return null;
  }
}
