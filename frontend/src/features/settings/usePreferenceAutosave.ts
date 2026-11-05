import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useAuth } from "@/features/auth/hooks";
import { getAuthSessionGeneration } from "@/stores/auth";

import { useUserPreferencesQuery } from "./queries";
import { useUpdateUserPreferences } from "./preferencesMutation";
import {
  preferenceQueueKey,
  persistPreferencePatch,
  type PreferencePatch,
} from "./preferenceQueueStorage";
import { parseWorkspace } from "./workspacePreferences";
export { PREFERENCE_QUEUE_CHANGED_EVENT } from "./preferenceQueueStorage";
import { parseQuickActionPreferences } from "./quickActions";
import {
  type AccentPreference,
  type ContrastPreference,
  type DensityPreference,
  type ThemePreference,
  type UserPreferencesUpdate,
  type UserPreferences,
} from "./types";

const PATCH_KEYS = new Set([
  "theme",
  "density",
  "contrast",
  "reduce_motion",
  "accent",
  "workspace",
  "quick_actions",
]);
const THEMES = new Set<ThemePreference>(["system", "light", "dark"]);
const DENSITIES = new Set<DensityPreference>(["auto", "compact", "comfortable", "touch"]);
const CONTRASTS = new Set<ContrastPreference>(["standard", "high"]);
const ACCENTS = new Set<AccentPreference>(["teal", "blue", "violet", "green", "amber", "rose"]);
export function usePreferenceAutosave(
  channel: string,
  options?: { preserveWorkspaceStartRoute?: boolean },
) {
  const { user } = useAuth();
  const authGeneration = getAuthSessionGeneration();
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
  const preserveWorkspaceStartRoute = options?.preserveWorkspaceStartRoute === true;

  const drain = useCallback(async () => {
    if (runningRef.current || versionRef.current === null || isEmpty(queueRef.current)) return;
    const generation = generationRef.current;
    const sessionGeneration = getAuthSessionGeneration();
    const active = () =>
      generation === generationRef.current && sessionGeneration === getAuthSessionGeneration();
    runningRef.current = true;
    setHasPending(true);

    while (!isEmpty(queueRef.current)) {
      const batch = queueRef.current;
      queueRef.current = {};
      inFlightRef.current = batch;
      persistPreferencePatch(scope, channel, { ...batch, ...queueRef.current });
      try {
        const payload: UserPreferencesUpdate = {
          expected_version: versionRef.current,
          ...batch,
        };
        const saved: UserPreferences = preserveWorkspaceStartRoute
          ? await mutateAsync(payload, { preserveWorkspaceStartRoute: true })
          : await mutateAsync(payload);
        if (!active()) return;
        versionRef.current = saved.version;
        inFlightRef.current = {};
        persistPreferencePatch(scope, channel, queueRef.current);
      } catch {
        if (!active()) return;
        queueRef.current = { ...batch, ...queueRef.current };
        inFlightRef.current = {};
        persistPreferencePatch(scope, channel, queueRef.current);
        runningRef.current = false;
        setHasPending(true);
        return;
      }
    }

    runningRef.current = false;
    setHasPending(false);
  }, [channel, mutateAsync, preserveWorkspaceStartRoute, scope]);

  useEffect(() => {
    generationRef.current += 1;
    runningRef.current = false;
    versionRef.current = null;
    const restored = readPendingPreferencePatch(scope, channel);
    queueRef.current = restored;
    inFlightRef.current = {};
    setHasPending(!isEmpty(queueRef.current));
    return () => {
      generationRef.current += 1;
    };
  }, [authGeneration, channel, scope]);

  useEffect(() => {
    if (!preferences.data) return;
    versionRef.current = preferences.data.version;
    void drain();
  }, [drain, preferences.data]);

  const enqueue = useCallback(
    (patch: PreferencePatch) => {
      queueRef.current = { ...queueRef.current, ...patch };
      persistPreferencePatch(scope, channel, { ...inFlightRef.current, ...queueRef.current });
      setHasPending(true);
      void drain();
    },
    [channel, drain, scope],
  );

  const retry = useCallback(async () => {
    const generation = generationRef.current;
    const sessionGeneration = getAuthSessionGeneration();
    const refreshed = await refetchPreferences();
    if (generation !== generationRef.current || sessionGeneration !== getAuthSessionGeneration())
      return;
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
    confirmedVersion: versionRef.current,
  };
}

export function readPendingPreferencePatch(scope: string, channel: string): PreferencePatch {
  try {
    const raw = window.localStorage.getItem(preferenceQueueKey(scope, channel));
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
  if ("quick_actions" in value) {
    const quickActions = parseQuickActionPreferences(value.quick_actions);
    if (quickActions === null) return null;
    patch.quick_actions = quickActions;
  }
  return patch;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSetMember<T extends string>(values: ReadonlySet<T>, value: unknown): value is T {
  return typeof value === "string" && values.has(value as T);
}

function isEmpty(patch: PreferencePatch): boolean {
  return Object.keys(patch).length === 0;
}
