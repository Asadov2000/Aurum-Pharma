import { useQuery } from "@tanstack/react-query";

import { useAuthStore } from "@/stores/auth";

import { fetchMe, listActiveSessions } from "./api";

export const meQueryKey = ["auth", "me"] as const;
export const activeSessionsQueryKey = ["auth", "sessions"] as const;

export function useMeQuery() {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useQuery({
    queryKey: meQueryKey,
    queryFn: ({ signal }) => fetchMe(signal),
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
