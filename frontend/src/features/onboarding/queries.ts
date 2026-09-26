import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getOnboardingOverview, launchPharmacy } from "./api";
import { type OnboardingOverview } from "./types";

export const onboardingKeys = {
  overview: (tenantId: string | undefined) => ["onboarding", "overview", tenantId] as const,
};

export function useOnboardingOverviewQuery(tenantId: string | undefined) {
  return useQuery({
    queryKey: onboardingKeys.overview(tenantId),
    queryFn: getOnboardingOverview,
    enabled: tenantId !== undefined,
    staleTime: 0,
    refetchOnMount: "always",
  });
}

export function useLaunchPharmacy(tenantId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (operationId: string) => launchPharmacy(operationId),
    retry: false,
    onSuccess: (result) => {
      qc.setQueryData<OnboardingOverview>(onboardingKeys.overview(tenantId), (current) =>
        current
          ? {
              ...current,
              tenant_status: result.status,
              launched_at: result.launched_at,
              can_launch: false,
              can_start_trial: false,
            }
          : current,
      );
      void qc.invalidateQueries({ queryKey: onboardingKeys.overview(tenantId) });
      void qc.invalidateQueries({ queryKey: ["billing"] });
    },
  });
}
