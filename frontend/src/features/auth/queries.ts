import { useQuery } from "@tanstack/react-query";

import { useAuthStore } from "@/stores/auth";

import { listActiveSessions } from "./sessionApi";

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
