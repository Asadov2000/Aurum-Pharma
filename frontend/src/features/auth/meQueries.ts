import { useQuery } from "@tanstack/react-query";

import { useAuthStore } from "@/stores/auth";

import { fetchMe } from "./api";

export const meQueryKey = ["auth", "me"] as const;

export function useMeQuery() {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useQuery({
    queryKey: meQueryKey,
    queryFn: ({ signal }) => fetchMe(signal),
    enabled: accessToken !== null,
    staleTime: 60_000,
    // Pick up remote permission changes without dropping a session on network errors.
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });
}
