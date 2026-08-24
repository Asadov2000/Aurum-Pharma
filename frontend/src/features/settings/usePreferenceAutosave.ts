import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useAuth } from "@/features/auth/hooks";

import { useUpdateUserPreferences, useUserPreferencesQuery } from "./queries";
import {
  type AccentPreference,
  type ContrastPreference,
  type DensityPreference,
  type ThemePreference,
  type UserPreferencesUpdate,
  type WorkspacePreferences,
} from "./types";

type PreferencePatch = Omit<UserPreferencesUpdate, "expected_version">;

const STORAGE_PREFIX = "aurum:preferences-pending:v1";
const PATCH_KEYS = new Set([
  "theme",
  "density",
  "contrast",
  "reduce_motion",
  "accent",
  "workspace",
]);
const THEMES = new Set<ThemePreference>(["system", "light", "dark"]);
const DENSITIES = new Set<DensityPreference>(["auto", "compact", "comfortable", "touch"]);
const CONTRASTS = new Set<ContrastPreference>(["standard", "high"]);
const ACCENTS = new Set<AccentPreference>(["teal", "blue", "violet", "green", "amber", "rose"]);
const DESKTOP_MODES = new Set<WorkspacePreferences["desktop_mode"]>([
  "auto",
  "compact",
  "expanded",
]);

export function usePreferenceAutosave(channel: string) {
  const { user } = useAuth();
  const scope = useMemo(
    () => `${user?.id ?? "anonymous"}:${user?.active_tenant_id ?? "global"}`,
    [user?.active_tenant_id, user?.id],
  );
  const preferences = useUserPreferencesQuery();
  const update = useUpdateUserPreferences();
  const queueRef = useRef<PreferencePatch>({});
  const inFlightRef = useRef<PreferencePatch>({});
  const versionRef = useRef<number | null>(null);
  const runningRef = useRef(false);
  const generationRef = useRef(0);
  const [hasPending, setHasPending] = useState(false);
  const mutateAsync = update.mutateAsync;
  const refetchPreferences = preferences.refetch;

  const drain = useCallback(async () => {
    if (runningRef.current || versionRef.current === null || isEmpty(queueRef.current)) return;
    const generation = generationRef.current;
    runningRef.current = true;
    setHasPending(true);

    while (!isEmpty(queueRef.current)) {
      const batch = queueRef.current;
      queueRef.current = {};
      inFlightRef.current = batch;
      persistPatch(scope, channel, { ...batch, ...queueRef.current });
      try {
        const saved = await mutateAsync({
          expected_version: versionRef.current,
          ...batch,
        });
        if (generation !== generationRef.current) return;
        versionRef.current = saved.version;
        inFlightRef.current = {};
        persistPatch(scope, channel, queueRef.current);
      } catch {
        if (generation !== generationRef.current) return;
        queueRef.current = { ...batch, ...queueRef.current };
        inFlightRef.current = {};
        persistPatch(scope, channel, queueRef.current);
        runningRef.current = false;
        setHasPending(true);
        return;
      }
    }

    runningRef.current = false;
    setHasPending(false);
  }, [channel, mutateAsync, scope]);

  useEffect(() => {
    generationRef.current += 1;
    runningRef.current = false;
    versionRef.current = null;
    const restored = readPatch(scope, channel);
    queueRef.current = restored;
    inFlightRef.current = {};
    setHasPending(!isEmpty(queueRef.current));
  }, [channel, scope]);

  useEffect(() => {
    if (!preferences.data) return;
    versionRef.current = preferences.data.version;
    void drain();
  }, [drain, preferences.data]);

  const enqueue = useCallback(
    (patch: PreferencePatch) => {
      queueRef.current = { ...queueRef.current, ...patch };
      persistPatch(scope, channel, { ...inFlightRef.current, ...queueRef.current });
      setHasPending(true);
      void drain();
    },
    [channel, drain, scope],
  );

  const retry = useCallback(async () => {
    const refreshed = await refetchPreferences();
    if (refreshed.data) versionRef.current = refreshed.data.version;
    void drain();
  }, [drain, refetchPreferences]);

  useEffect(() => {
    const onOnline = () => void retry();
    window.addEventListener("online", onOnline);
    return () => window.removeEventListener("online", onOnline);
  }, [retry]);

  return {
    preferences,
    enqueue,
    retry,
    hasPending: hasPending || update.isPending,
    error: update.error,
  };
}

function storageKey(scope: string, channel: string): string {
  return `${STORAGE_PREFIX}:${encodeURIComponent(scope)}:${encodeURIComponent(channel)}`;
}

function readPatch(scope: string, channel: string): PreferencePatch {
  try {
    const raw = window.localStorage.getItem(storageKey(scope, channel));
    if (raw === null) return {};
    return parsePendingPatch(JSON.parse(raw)) ?? {};
  } catch {
    return {};
  }
}

function parsePendingPatch(value: unknown): PreferencePatch | null {
  if (!isRecord(value) || Object.keys(value).some((key) => !PATCH_KEYS.has(key))) return null;

  const patch: PreferencePatch = {};
  if ("theme" in value) {
    if (!isSetMember(THEMES, value.theme)) return null;
    patch.theme = value.theme;
  }
  if ("density" in value) {
    if (!isSetMember(DENSITIES, value.density)) return null;
    patch.density = value.density;
  }
  if ("contrast" in value) {
    if (!isSetMember(CONTRASTS, value.contrast)) return null;
    patch.contrast = value.contrast;
  }
  if ("reduce_motion" in value) {
    if (typeof value.reduce_motion !== "boolean") return null;
    patch.reduce_motion = value.reduce_motion;
  }
  if ("accent" in value) {
    if (!isSetMember(ACCENTS, value.accent)) return null;
    patch.accent = value.accent;
  }
  if ("workspace" in value) {
    const workspace = parseWorkspace(value.workspace);
    if (workspace === null) return null;
    patch.workspace = workspace;
  }
  return patch;
}

function parseWorkspace(value: unknown): WorkspacePreferences | null {
  if (!isRecord(value) || !isSetMember(DESKTOP_MODES, value.desktop_mode)) return null;
  const hiddenRoutes = parseRouteList(value.hidden_routes);
  const favoriteRoutes = parseRouteList(value.favorite_routes);
  const routeOrder = parseRouteList(value.route_order);
  if (
    hiddenRoutes === null ||
    favoriteRoutes === null ||
    routeOrder === null ||
    typeof value.start_route !== "string" ||
    value.start_route.length > 128
  ) {
    return null;
  }
  return {
    desktop_mode: value.desktop_mode,
    hidden_routes: hiddenRoutes,
    favorite_routes: favoriteRoutes,
    route_order: routeOrder,
    start_route: value.start_route,
  };
}

function parseRouteList(value: unknown): string[] | null {
  return Array.isArray(value) &&
    value.length <= 64 &&
    value.every((item) => typeof item === "string")
    ? value
    : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSetMember<T extends string>(values: ReadonlySet<T>, value: unknown): value is T {
  return typeof value === "string" && values.has(value as T);
}

function persistPatch(scope: string, channel: string, patch: PreferencePatch): void {
  try {
    if (isEmpty(patch)) window.localStorage.removeItem(storageKey(scope, channel));
    else window.localStorage.setItem(storageKey(scope, channel), JSON.stringify(patch));
  } catch {
    // The in-memory queue remains authoritative until this page is closed.
  }
}

function isEmpty(patch: PreferencePatch): boolean {
  return Object.keys(patch).length === 0;
}
