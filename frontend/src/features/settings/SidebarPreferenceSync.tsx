import { useEffect, useRef, useState } from "react";

import { getAuthSessionGeneration } from "@/stores/auth";

import { readPendingWorkspace } from "./preferenceQueueStorage";
import { type WorkspacePreferences } from "./types";
import { usePreferenceAutosave } from "./usePreferenceAutosave";

export interface SidebarSyncCommand {
  scope: string;
  session: number;
  revision: number;
  workspace: WorkspacePreferences;
}

export interface SidebarPreferenceSyncProps {
  command: SidebarSyncCommand;
  onState: (
    command: SidebarSyncCommand,
    pending: boolean,
    error: unknown,
    confirmedVersion?: number | null,
  ) => void;
}

export default function SidebarPreferenceSync({
  command,
  onState,
}: SidebarPreferenceSyncProps): null {
  const autosave = usePreferenceAutosave("sidebar-navigation", {
    preserveWorkspaceStartRoute: true,
  });
  const { enqueue } = autosave;
  const submittedRef = useRef<number | null>(null);
  const [submitted, setSubmitted] = useState<number | null>(null);

  useEffect(() => {
    if (command.session !== getAuthSessionGeneration() || submittedRef.current === command.revision)
      return;
    // The autosave hook has already restored its durable queue on first mount.
    const restored =
      submittedRef.current === null &&
      JSON.stringify(readPendingWorkspace(command.scope, "sidebar-navigation")) ===
        JSON.stringify(command.workspace);
    submittedRef.current = command.revision;
    if (!restored) enqueue({ workspace: command.workspace });
    setSubmitted(command.revision);
  }, [command, enqueue]);

  useEffect(() => {
    if (submitted !== command.revision || command.session !== getAuthSessionGeneration()) return;
    onState(command, autosave.hasPending, autosave.error, autosave.confirmedVersion);
  }, [autosave.confirmedVersion, autosave.error, autosave.hasPending, command, onState, submitted]);

  return null;
}
