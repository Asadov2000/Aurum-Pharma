import { useCallback, useEffect, useState } from "react";

import { useAuth } from "@/features/auth/hooks";

import {
  defaultQuickActionPreferences,
  parseQuickActionMenuPreferences,
  type QuickActionMenuPreferences,
  type QuickActionSection,
} from "./quickActions";
import {
  loadQuickActionPreferences,
  QUICK_ACTIONS_CHANGED_EVENT,
  saveQuickActionPreferences,
} from "./quickActionStorage";
import {
  PREFERENCE_QUEUE_CHANGED_EVENT,
  readPendingPreferencePatch,
  usePreferenceAutosave,
} from "./usePreferenceAutosave";

export function useQuickActionPreferences(section: QuickActionSection) {
  const { user } = useAuth();
  const scope = `${user?.id ?? "anonymous"}:${user?.active_tenant_id ?? "global"}`;
  const channel = `quick-actions:${section}`;
  const autosave = usePreferenceAutosave(channel);
  const { enqueue } = autosave;
  const [, refresh] = useState(0);
  const [localSave, setLocalSave] = useState<{
    scope: string;
    section: QuickActionSection;
    config: QuickActionMenuPreferences;
  } | null>(null);
  const pending = readPendingPreferencePatch(scope, channel).quick_actions?.[section];
  const memoryPending =
    autosave.hasPending && localSave?.scope === scope && localSave.section === section
      ? localSave.config
      : null;
  const server = parseQuickActionMenuPreferences(
    autosave.preferences.data?.quick_actions?.[section],
    section,
  );
  const config =
    memoryPending ??
    pending ??
    server ??
    loadQuickActionPreferences(scope, section) ??
    defaultQuickActionPreferences(section);

  useEffect(() => {
    if (
      localSave &&
      (!autosave.hasPending || localSave.scope !== scope || localSave.section !== section)
    ) {
      setLocalSave(null);
    }
  }, [autosave.hasPending, localSave, scope, section]);

  useEffect(() => {
    const onChange = (event: Event) => {
      const detail = (event as CustomEvent<{ scope?: string }>).detail;
      if (!detail || detail.scope === scope) refresh((value) => value + 1);
    };
    window.addEventListener(QUICK_ACTIONS_CHANGED_EVENT, onChange);
    window.addEventListener(PREFERENCE_QUEUE_CHANGED_EVENT, onChange);
    window.addEventListener("storage", onChange);
    return () => {
      window.removeEventListener(QUICK_ACTIONS_CHANGED_EVENT, onChange);
      window.removeEventListener(PREFERENCE_QUEUE_CHANGED_EVENT, onChange);
      window.removeEventListener("storage", onChange);
    };
  }, [scope]);

  useEffect(() => {
    if (
      !autosave.preferences.data ||
      autosave.hasPending ||
      readPendingPreferencePatch(scope, channel).quick_actions?.[section]
    )
      return;
    const next = parseQuickActionMenuPreferences(
      autosave.preferences.data.quick_actions?.[section],
      section,
    );
    // Older servers may omit the optional field; retain this device's valid cache.
    if (next) saveQuickActionPreferences(scope, section, next);
  }, [autosave.hasPending, autosave.preferences.data, channel, scope, section]);

  const save = useCallback(
    (value: QuickActionMenuPreferences) => {
      const next = parseQuickActionMenuPreferences(value, section);
      if (!next || !user) return;
      setLocalSave({ scope, section, config: next });
      enqueue({ quick_actions: { [section]: next } });
      saveQuickActionPreferences(scope, section, next);
    },
    [enqueue, scope, section, user],
  );

  return { config, scope, save, hasPending: autosave.hasPending, error: autosave.error };
}
