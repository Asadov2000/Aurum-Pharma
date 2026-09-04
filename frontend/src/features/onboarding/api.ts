import { api } from "@/lib/api";

import { type OnboardingOverview, type StartTrialResponse } from "./types";

const ONBOARDING_WRITE_TIMEOUT_MS = 15_000;

export async function getOnboardingOverview(): Promise<OnboardingOverview> {
  const { data } = await api.get<OnboardingOverview>("/onboarding/overview");
  return data;
}

export async function startTrial(operationId: string): Promise<StartTrialResponse> {
  const { data } = await api.post<StartTrialResponse>(
    "/onboarding/start-trial",
    { operation_id: operationId },
    { timeout: ONBOARDING_WRITE_TIMEOUT_MS },
  );
  return data;
}

export async function markReceiptPrintTested(): Promise<void> {
  await api.post("/onboarding/tasks/receipt-print-tested");
}
