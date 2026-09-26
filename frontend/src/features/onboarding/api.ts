import { api } from "@/lib/api";

import { type OnboardingOverview, type LaunchResponse } from "./types";

const ONBOARDING_WRITE_TIMEOUT_MS = 15_000;

export async function getOnboardingOverview(): Promise<OnboardingOverview> {
  const { data } = await api.get<OnboardingOverview>("/onboarding/overview");
  return data;
}

export async function launchPharmacy(operationId: string): Promise<LaunchResponse> {
  const { data } = await api.post<LaunchResponse>(
    "/onboarding/launch",
    { operation_id: operationId },
    { timeout: ONBOARDING_WRITE_TIMEOUT_MS },
  );
  return data;
}

export async function markReceiptPrintTested(): Promise<void> {
  await api.post("/onboarding/tasks/receipt-print-tested");
}
