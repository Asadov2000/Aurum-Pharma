import { api } from "@/lib/api";

import {
  type Checklist,
  type StartTrialResponse,
  type WizardState,
} from "./types";

export async function getWizard(): Promise<WizardState> {
  const { data } = await api.get<WizardState>("/onboarding/wizard");
  return data;
}

export async function submitWizardStep(
  step: number,
  payload: Record<string, unknown>,
): Promise<WizardState> {
  const { data } = await api.post<WizardState>(
    `/onboarding/wizard/step/${step}`,
    { data: payload },
  );
  return data;
}

export async function getChecklist(): Promise<Checklist> {
  const { data } = await api.get<Checklist>("/onboarding/checklist");
  return data;
}

export async function startTrial(): Promise<StartTrialResponse> {
  const { data } = await api.post<StartTrialResponse>("/onboarding/start-trial");
  return data;
}
