import { api } from "@/lib/api";

import { type OnboardingOverview, type StartTrialResponse } from "./types";

export async function getOnboardingOverview(): Promise<OnboardingOverview> {
  const { data } = await api.get<OnboardingOverview>("/onboarding/overview");
  return data;
}

export async function startTrial(operationId: string): Promise<StartTrialResponse> {
  const { data } = await api.post<StartTrialResponse>("/onboarding/start-trial", {
    operation_id: operationId,
  });
  return data;
}

export async function markReceiptPrintTested(): Promise<void> {
  await api.post("/onboarding/tasks/receipt-print-tested");
}
