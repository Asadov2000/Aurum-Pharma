import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { z } from "zod";

import { useAuth } from "@/features/auth/hooks";

import { useUpdateUserPreferences, useUserPreferencesQuery } from "./queries";
import { type UserPreferencesUpdate } from "./types";

type PreferencePatch = Omit<UserPreferencesUpdate, "expected_version">;

const workspaceSchema = z.object({
  desktop_mode: z.enum(["auto", "compact", "expanded"]),
  hidden_routes: z.array(z.string()).max(64),
  favorite_routes: z.array(z.string()).max(64),
  route_order: z.array(z.string()).max(64),
  start_route: z.string().max(128),
});

const pendingPatchSchema = z
  .object({
    theme: z.enum(["system", "light", "dark"]).optional(),
    density: z.enum(["auto", "compact", "comfortable", "touch"]).optional(),
    contrast: z.enum(["standard", "high"]).optional(),
    reduce_motion: z.boolean().optional(),
    accent: z.enum(["teal", "blue", "violet", "green", "amber", "rose"]).optional(),
    workspace: workspaceSchema.optional(),
  })
  .strict();

const STORAGE_PREFIX = "aurum:preferences-pending:v1";

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
    const parsed = pendingPatchSchema.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : {};
  } catch {
    return {};
  }
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
