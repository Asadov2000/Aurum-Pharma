import { useMutation, useQueryClient } from "@tanstack/react-query";

import { activeSessionsQueryKey } from "./queries";
import { revokeActiveSession, revokeOtherSessions } from "./sessionApi";

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
