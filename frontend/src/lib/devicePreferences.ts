import { useCallback, useEffect, useMemo, useState } from "react";

import { useAuth } from "@/features/auth/hooks";
import { useAuthStore } from "@/stores/auth";

export type DevicePosMode = "auto" | "keyboard" | "touch";
export type DeviceReceiptWidth = "58" | "80" | "A4";

export interface DevicePreferences {
  posMode: DevicePosMode;
  scannerSound: boolean;
  receiptWidth: DeviceReceiptWidth;
  lastRegisterId: string | null;
}

const STORAGE_PREFIX = "aurum:device:v1";
const LEGACY_MODE_KEY = "pos:mode";
const LEGACY_SOUND_KEY = "pos:beep";
const LEGACY_REGISTER_KEY = "pos:lastRegisterId";
const LEGACY_MIGRATION_KEY = `${STORAGE_PREFIX}:legacy-migrated`;
export const DEVICE_PREFERENCES_CHANGED_EVENT = "aurum:device-preferences-changed";

export function devicePreferencesScope(
  userId: string | undefined,
  tenantId: string | null | undefined,
): string {
  return `${userId ?? "anonymous"}:${tenantId ?? "global"}`;
}

export function devicePreferencesKey(scope: string): string {
  return `${STORAGE_PREFIX}:${encodeURIComponent(scope)}`;
}

export function currentDevicePreferencesScope(): string {
  const user = useAuthStore.getState().user;
  return devicePreferencesScope(user?.id, user?.active_tenant_id);
}

export function defaultDevicePreferences(): DevicePreferences {
  return {
    posMode: "auto",
    scannerSound: false,
    receiptWidth: "80",
    lastRegisterId: null,
  };
}

export function loadDevicePreferences(scope: string): DevicePreferences {
  try {
    const stored = window.localStorage.getItem(devicePreferencesKey(scope));
    if (stored !== null) return parseDevicePreferences(JSON.parse(stored));

    const mayClaimLegacySettings =
      !scope.startsWith("anonymous:") && window.localStorage.getItem(LEGACY_MIGRATION_KEY) === null;
    const migrated = mayClaimLegacySettings
      ? parseDevicePreferences({
          posMode: window.localStorage.getItem(LEGACY_MODE_KEY),
          scannerSound: window.localStorage.getItem(LEGACY_SOUND_KEY) === "1",
          lastRegisterId: window.localStorage.getItem(LEGACY_REGISTER_KEY),
        })
      : defaultDevicePreferences();
    // Initial migration must not emit the same-tab synchronization event:
    // listeners may be loading this exact scope already.
    window.localStorage.setItem(devicePreferencesKey(scope), JSON.stringify(migrated));
    if (mayClaimLegacySettings) window.localStorage.setItem(LEGACY_MIGRATION_KEY, scope);
    return migrated;
  } catch {
    return defaultDevicePreferences();
  }
}

export function saveDevicePreferences(scope: string, value: DevicePreferences): void {
  const parsed = parseDevicePreferences(value);
  try {
    window.localStorage.setItem(devicePreferencesKey(scope), JSON.stringify(parsed));
  } catch {
    // Device preferences still apply until this browser session ends.
    return;
  }
  window.dispatchEvent(new CustomEvent(DEVICE_PREFERENCES_CHANGED_EVENT, { detail: { scope } }));
}

export function useDevicePreferences(): {
  preferences: DevicePreferences;
  updatePreferences: (patch: Partial<DevicePreferences>) => void;
  resetPreferences: () => void;
} {
  const { user } = useAuth();
  const scope = useMemo(
    () => devicePreferencesScope(user?.id, user?.active_tenant_id),
    [user?.active_tenant_id, user?.id],
  );
  const [preferences, setPreferences] = useState<DevicePreferences>(() =>
    loadDevicePreferences(scope),
  );

  useEffect(() => {
    setPreferences(loadDevicePreferences(scope));
    const key = devicePreferencesKey(scope);
    const sync = () => setPreferences(loadDevicePreferences(scope));
    const onStorage = (event: StorageEvent) => {
      if (event.key === key) sync();
    };
    const onChanged = (event: Event) => {
      const detail = (event as CustomEvent<{ scope?: string }>).detail;
      if (detail?.scope === scope) sync();
    };
    window.addEventListener("storage", onStorage);
    window.addEventListener(DEVICE_PREFERENCES_CHANGED_EVENT, onChanged);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(DEVICE_PREFERENCES_CHANGED_EVENT, onChanged);
    };
  }, [scope]);

  const updatePreferences = useCallback(
    (patch: Partial<DevicePreferences>) => {
      const next = parseDevicePreferences({ ...loadDevicePreferences(scope), ...patch });
      setPreferences(next);
      saveDevicePreferences(scope, next);
    },
    [scope],
  );

  const resetPreferences = useCallback(() => {
    const next = defaultDevicePreferences();
    setPreferences(next);
    saveDevicePreferences(scope, next);
  }, [scope]);

  return { preferences, updatePreferences, resetPreferences };
}

function parseDevicePreferences(value: unknown): DevicePreferences {
  if (typeof value !== "object" || value === null) return defaultDevicePreferences();
  const candidate = value as Record<string, unknown>;
  return {
    posMode:
      candidate.posMode === "keyboard" || candidate.posMode === "touch"
        ? candidate.posMode
        : "auto",
    scannerSound: candidate.scannerSound === true,
    receiptWidth:
      candidate.receiptWidth === "58" || candidate.receiptWidth === "A4"
        ? candidate.receiptWidth
        : "80",
    lastRegisterId:
      typeof candidate.lastRegisterId === "string" && candidate.lastRegisterId.length <= 128
        ? candidate.lastRegisterId
        : null,
  };
}
