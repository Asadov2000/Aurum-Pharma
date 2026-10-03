import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useAuthStore } from "@/stores/auth";

import { listActiveSessions, revokeActiveSession, revokeOtherSessions } from "./api";

// Session management belongs to the lazy security page, not the login startup.
export { meQueryKey, useMeQuery } from "./meQueries";
export const activeSessionsQueryKey = ["auth", "sessions"] as const;

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
