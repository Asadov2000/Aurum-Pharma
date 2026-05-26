import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getChecklist, getWizard, startTrial, submitWizardStep } from "./api";

export const onboardingKeys = {
  wizard: ["onboarding", "wizard"] as const,
  checklist: ["onboarding", "checklist"] as const,
};

export function useWizardQuery(enabled = true) {
  return useQuery({
    queryKey: onboardingKeys.wizard,
    queryFn: getWizard,
    enabled,
  });
}

export function useChecklistQuery(enabled = true) {
  return useQuery({
    queryKey: onboardingKeys.checklist,
    queryFn: getChecklist,
    enabled,
  });
}

export function useSubmitStep() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { step: number; payload: Record<string, unknown> }) =>
      submitWizardStep(args.step, args.payload),
    onSuccess: (data) => {
      qc.setQueryData(onboardingKeys.wizard, data);
    },
  });
}

export function useStartTrial() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: startTrial,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: onboardingKeys.checklist });
      // Subscription was created — refresh billing too.
      void qc.invalidateQueries({ queryKey: ["billing"] });
    },
  });
}
