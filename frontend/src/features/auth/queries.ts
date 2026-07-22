import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useAuthStore } from "@/stores/auth";

import { fetchMe, listActiveSessions, revokeActiveSession, revokeOtherSessions } from "./api";

export const meQueryKey = ["auth", "me"] as const;
export const activeSessionsQueryKey = ["auth", "sessions"] as const;

export function useMeQuery() {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useQuery({
    queryKey: meQueryKey,
    queryFn: fetchMe,
    enabled: accessToken !== null,
    staleTime: 60_000,
  });
}

export function useActiveSessionsQuery() {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useQuery({
    queryKey: activeSessionsQueryKey,
    queryFn: listActiveSessions,
    enabled: accessToken !== null,
    staleTime: 15_000,
  });
}

export function useRevokeActiveSession() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) => revokeActiveSession(sessionId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: activeSessionsQueryKey });
    },
  });
}

export function useRevokeOtherSessions() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: revokeOtherSessions,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: activeSessionsQueryKey });
    },
  });
}
