import { isAxiosError } from "axios";
import { QueryClient } from "@tanstack/react-query";

const QUERY_STALE_TIME_MS = 30_000;
const QUERY_GC_TIME_MS = 15 * 60_000;

export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  if (failureCount > 0 || !isAxiosError(error)) {
    return false;
  }

  const status = error.response?.status;
  if (status === undefined) {
    return true;
  }

  return status === 408 || status === 425 || status === 429 || status >= 500;
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      gcTime: QUERY_GC_TIME_MS,
      retry: shouldRetryQuery,
      refetchOnWindowFocus: false,
      staleTime: QUERY_STALE_TIME_MS,
    },
  },
});
