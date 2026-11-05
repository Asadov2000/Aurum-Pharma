import { createElement, lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";

import {
  defaultSidebarPreferences,
  loadSidebarPreferences,
  parseSidebarPreferences,
  saveSidebarPreferences,
  SIDEBAR_PREFERENCES_CHANGED_EVENT,
  sidebarStorageKey,
  type SidebarPreferences,
} from "@/components/layout/sidebarPreferences";
import { useAuth } from "@/features/auth/hooks";
import { getAuthSessionGeneration } from "@/stores/auth";
import {
  PREFERENCE_QUEUE_CHANGED_EVENT,
  persistPreferencePatch,
  readPendingWorkspace,
} from "./preferenceQueueStorage";
import { useUserPreferencesQuery } from "./queries";
import { type SidebarPreferenceSyncProps, type SidebarSyncCommand } from "./SidebarPreferenceSync";
import { type WorkspacePreferences } from "./types";

const CHANNEL = "sidebar-navigation";
const SERVER_MARKER_PREFIX = "aurum:sidebar-server:v1";
let importFailed = false;

function createSync() {
  return lazy(() =>
    (navigator.onLine
      ? import("./SidebarPreferenceSync")
      : Promise.reject(
          new Error("Меню сохранено на устройстве. Синхронизация продолжится при подключении."),
        )
    ).catch((error: unknown) => {
      importFailed = true;
      return {
        default: function Unavailable({ command, onState }: SidebarPreferenceSyncProps) {
          useEffect(() => onState(command, true, error), [command, onState]);
          return null;
        },
      };
    }),
  );
}

export function useSidebarPreferences() {
  const { user } = useAuth();
  const scope = `${user?.id ?? "anonymous"}:${user?.active_tenant_id ?? "global"}`;
  const session = getAuthSessionGeneration();
  const serverQuery = useUserPreferencesQuery();
  const ownerRef = useRef({ scope, session });
  ownerRef.current = { scope, session };
  const mountedRef = useRef(false);
  const revisionRef = useRef(0);
  const migratedRef = useRef<string | null>(null);
  const confirmedRef = useRef<{ scope: string; session: number; version: number } | null>(null);
  const [Sync, setSync] = useState(createSync);
  const [local, setLocal] = useState(() => ({ scope, config: loadSidebarPreferences(scope) }));
  const [command, setCommand] = useState<SidebarSyncCommand | null>(() => {
    const workspace = user ? readPendingWorkspace(scope, CHANNEL) : null;
    return workspace ? { scope, session, revision: ++revisionRef.current, workspace } : null;
  });
  const commandRef = useRef(command);
  commandRef.current = command;
  const [failure, setFailure] = useState<{ scope: string; session: number; error: unknown } | null>(
    null,
  );
  const [, refresh] = useState(0);
  const durable = readPendingWorkspace(scope, CHANNEL);
  const owned = command?.scope === scope && command.session === session ? command : null;
  const pending = owned?.workspace ?? durable;
  const preferences = pending
    ? fromWorkspace(pending)
    : local.scope === scope
      ? local.config
      : loadSidebarPreferences(scope);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (!user || owned || !durable) return;
    setCommand({ scope, session, revision: ++revisionRef.current, workspace: durable });
  }, [durable, owned, scope, session, user]);

  useEffect(() => {
    const onChange = (event: Event) => {
      const detail = (event as CustomEvent<{ scope?: string; preferences?: SidebarPreferences }>)
        .detail;
      if (event instanceof StorageEvent && event.key !== null) {
        if (
          event.key !== sidebarStorageKey(scope) &&
          !event.key.startsWith("aurum:preferences-pending:v1:")
        )
          return;
      } else if (detail?.scope !== undefined && detail.scope !== scope) return;
      if (event.type !== PREFERENCE_QUEUE_CHANGED_EVENT) {
        if (event instanceof StorageEvent) {
          if (event.key === null || event.key === sidebarStorageKey(scope))
            setLocal({ scope, config: loadSidebarPreferences(scope) });
        } else if (detail?.preferences) {
          setLocal({ scope, config: parseSidebarPreferences(detail.preferences) });
        }
      }
      refresh((value) => value + 1);
    };
    const onOnline = () => {
      if (importFailed) {
        importFailed = false;
        setSync(createSync());
      }
    };
    window.addEventListener("storage", onChange);
    window.addEventListener(SIDEBAR_PREFERENCES_CHANGED_EVENT, onChange);
    window.addEventListener(PREFERENCE_QUEUE_CHANGED_EVENT, onChange);
    window.addEventListener("online", onOnline);
    return () => {
      window.removeEventListener("storage", onChange);
      window.removeEventListener(SIDEBAR_PREFERENCES_CHANGED_EVENT, onChange);
      window.removeEventListener(PREFERENCE_QUEUE_CHANGED_EVENT, onChange);
      window.removeEventListener("online", onOnline);
    };
  }, [scope]);

  const save = useCallback(
    (value: SidebarPreferences) => {
      if (
        !user ||
        !mountedRef.current ||
        ownerRef.current.scope !== scope ||
        ownerRef.current.session !== session ||
        getAuthSessionGeneration() !== session
      )
        return;
      const config = parseSidebarPreferences(value);
      const workspace = toWorkspace(config, serverQuery.data?.workspace.start_route);
      const next = { scope, session, revision: ++revisionRef.current, workspace };
      commandRef.current = next;
      setCommand(next);
      setFailure(null);
      setLocal({ scope, config });
      // The intent is durable before the synchronization code is loaded.
      persistPreferencePatch(scope, CHANNEL, { workspace });
      saveSidebarPreferences(scope, config);
      if (importFailed) {
        importFailed = false;
        setSync(createSync());
      }
    },
    [scope, serverQuery.data?.workspace.start_route, session, user],
  );

  const onState = useCallback(
    (
      completed: SidebarSyncCommand,
      hasPending: boolean,
      error: unknown,
      version?: number | null,
    ) => {
      if (
        !mountedRef.current ||
        ownerRef.current.scope !== completed.scope ||
        getAuthSessionGeneration() !== completed.session ||
        commandRef.current?.revision !== completed.revision
      )
        return;
      setFailure(error ? { scope: completed.scope, session: completed.session, error } : null);
      if (!hasPending) {
        setLocal({ scope: completed.scope, config: fromWorkspace(completed.workspace) });
        if (version !== null && version !== undefined)
          confirmedRef.current = { scope: completed.scope, session: completed.session, version };
        commandRef.current = null;
        setCommand(null);
      }
    },
    [],
  );

  useEffect(() => {
    if (
      !user ||
      !serverQuery.data ||
      pending ||
      (confirmedRef.current?.scope === scope &&
        confirmedRef.current.session === session &&
        serverQuery.data.version < confirmedRef.current.version)
    )
      return;
    const server = fromWorkspace(serverQuery.data.workspace);
    const cached = loadSidebarPreferences(scope);
    if (
      migratedRef.current !== scope &&
      !hasServerMarker(scope) &&
      sameSidebar(server, defaultSidebarPreferences()) &&
      !sameSidebar(cached, defaultSidebarPreferences())
    ) {
      migratedRef.current = scope;
      save(cached);
      return;
    }
    migratedRef.current = scope;
    markServerSynced(scope);
    setLocal({ scope, config: server });
    if (!sameSidebar(cached, server)) saveSidebarPreferences(scope, server);
  }, [pending, save, scope, serverQuery.data, session, user]);

  const persistence = owned
    ? createElement(
        Suspense,
        { fallback: null },
        createElement(Sync, { key: `${scope}:${session}`, command: owned, onState }),
      )
    : null;
  return {
    preferences,
    scope,
    save,
    hasPending: Boolean(pending),
    error: failure?.scope === scope && failure.session === session ? failure.error : null,
    persistence,
  };
}

function fromWorkspace(workspace: WorkspacePreferences): SidebarPreferences {
  return parseSidebarPreferences({
    desktopMode: workspace.desktop_mode,
    hiddenRoutes: workspace.hidden_routes,
    favoriteRoutes: workspace.favorite_routes,
    routeOrder: workspace.route_order,
  });
}

function toWorkspace(value: SidebarPreferences, startRoute = "/"): WorkspacePreferences {
  return {
    desktop_mode: value.desktopMode,
    hidden_routes: value.hiddenRoutes,
    favorite_routes: value.favoriteRoutes,
    route_order: value.routeOrder,
    start_route: startRoute,
  };
}

function sameSidebar(left: SidebarPreferences, right: SidebarPreferences): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function hasServerMarker(scope: string): boolean {
  try {
    return (
      window.localStorage.getItem(`${SERVER_MARKER_PREFIX}:${encodeURIComponent(scope)}`) === "1"
    );
  } catch {
    return false;
  }
}

function markServerSynced(scope: string): void {
  try {
    window.localStorage.setItem(`${SERVER_MARKER_PREFIX}:${encodeURIComponent(scope)}`, "1");
  } catch {
    // The component marker prevents repeated migration when storage is blocked.
  }
}
