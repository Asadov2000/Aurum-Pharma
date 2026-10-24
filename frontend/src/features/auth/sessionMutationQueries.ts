import { useMutation, useQueryClient } from "@tanstack/react-query";

import { revokeActiveSession, revokeOtherSessions } from "./api";
import { activeSessionsQueryKey } from "./queries";

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
