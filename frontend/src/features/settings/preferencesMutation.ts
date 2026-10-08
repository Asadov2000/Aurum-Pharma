import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import { useAuth } from "@/features/auth/hooks";
import { getAuthSessionGeneration, useAuthStore } from "@/stores/auth";

import { updateUserPreferences } from "./api";
import { settingsKeys } from "./queries";
import { type UserPreferences, type UserPreferencesUpdate } from "./types";

export function useUpdateUserPreferences() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const queryKey = settingsKeys.preferences(user?.id, user?.active_tenant_id);
  const generation = getAuthSessionGeneration();
  const userId = user?.id;
  const tenantId = user?.active_tenant_id;
  const mutation = useMutation({
    mutationKey: settingsKeys.preferencesUpdate,
    scope: {
      id: `user-preferences:${user?.id ?? "anonymous"}:${user?.active_tenant_id ?? "global"}`,
    },
    mutationFn: (command: {
      payload: UserPreferencesUpdate;
      userId: string | undefined;
      tenantId: string | null | undefined;
      generation: number;
      preserveWorkspaceStartRoute?: boolean;
    }) => {
      const current = useAuthStore.getState().user;
      if (
        !command.userId ||
        command.generation !== getAuthSessionGeneration() ||
        current?.id !== command.userId ||
        current.active_tenant_id !== command.tenantId
      ) {
        throw new Error("Сессия изменилась. Настройки сохранены для прежнего аккаунта.");
      }
      const cached = queryClient.getQueryData<UserPreferences>(queryKey);
      const payload = command.payload;
      if (command.preserveWorkspaceStartRoute && payload.workspace && !cached) {
        throw new Error("Сначала загрузите настройки аккаунта.");
      }
      return updateUserPreferences({
        ...payload,
        ...(command.preserveWorkspaceStartRoute && payload.workspace && cached
          ? { workspace: { ...payload.workspace, start_route: cached.workspace.start_route } }
          : {}),
        expected_version: cached?.version ?? payload.expected_version,
      });
    },
    onSuccess: (data, command) => {
      const current = useAuthStore.getState().user;
      if (
        command.generation !== getAuthSessionGeneration() ||
        current?.id !== command.userId ||
        current?.active_tenant_id !== command.tenantId
      )
        return;
      queryClient.setQueryData(queryKey, data);
    },
  });
  const { mutate: runMutation, mutateAsync: runMutationAsync } = mutation;
  const mutateAsync = useCallback(
    (payload: UserPreferencesUpdate, options?: { preserveWorkspaceStartRoute?: boolean }) =>
      runMutationAsync({ payload, userId, tenantId, generation, ...options }),
    [generation, runMutationAsync, tenantId, userId],
  );
  const mutate = useCallback(
    (payload: UserPreferencesUpdate) => runMutation({ payload, userId, tenantId, generation }),
    [generation, runMutation, tenantId, userId],
  );
  const ownMutation =
    mutation.variables?.userId === userId &&
    mutation.variables?.tenantId === tenantId &&
    mutation.variables?.generation === generation;
  return {
    ...mutation,
    isPending: ownMutation && mutation.isPending,
    isSuccess: ownMutation && mutation.isSuccess,
    isError: ownMutation && mutation.isError,
    error: ownMutation ? mutation.error : null,
    data: ownMutation ? mutation.data : undefined,
    mutate,
    mutateAsync,
  };
}
