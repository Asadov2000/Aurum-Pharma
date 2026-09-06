import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/auth";
import { fetchMfaSettings, dismissMfaPrompt } from "./accountSecurityApi";
import { type MfaSettings } from "./types";

export const mfaSettingsQueryKey = ["auth", "mfa-settings"] as const;

export function useMfaSettingsQuery(enabled = true) {
  const accessToken = useAuthStore((state) => state.accessToken);
  return useQuery({
    queryKey: mfaSettingsQueryKey,
    queryFn: fetchMfaSettings,
    enabled: enabled && accessToken !== null,
    staleTime: 60_000,
  });
}

export function useDismissMfaPrompt() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: dismissMfaPrompt,
    onSuccess: () =>
      client.setQueryData<MfaSettings>(mfaSettingsQueryKey, (value) =>
        value ? { ...value, prompt_pending: false } : value,
      ),
  });
}
