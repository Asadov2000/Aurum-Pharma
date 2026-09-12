import { useQuery } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/auth";
import { fetchAccountIdentity, fetchLoginMethods } from "./identityApi";

export const accountIdentityQueryKey = ["auth", "identity"] as const;

export function useAccountIdentityQuery() {
  const token = useAuthStore((state) => state.accessToken);
  return useQuery({
    queryKey: accountIdentityQueryKey,
    queryFn: fetchAccountIdentity,
    enabled: token !== null,
    staleTime: 60_000,
  });
}

export function useLoginMethodsQuery() {
  return useQuery({
    queryKey: ["auth", "methods"],
    queryFn: fetchLoginMethods,
    staleTime: 60_000,
    retry: false,
  });
}
