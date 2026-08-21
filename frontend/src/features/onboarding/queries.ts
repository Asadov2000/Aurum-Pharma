import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getOnboardingOverview, startTrial } from "./api";

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

export function useStartTrial(tenantId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (operationId: string) => startTrial(operationId),
    retry: false,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: onboardingKeys.overview(tenantId) });
      void qc.invalidateQueries({ queryKey: ["billing"] });
    },
  });
}
