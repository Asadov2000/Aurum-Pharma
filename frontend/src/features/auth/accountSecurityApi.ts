import { api, withoutRefresh, withoutStepUp } from "@/lib/api";
import {
  type LoginCodeResponse,
  type MfaCodeRequest,
  type MfaDisableRequest,
  type MfaSettings,
  type MfaSettingsEnrollment,
  type TokenPair,
} from "./types";

export async function fetchMfaSettings(): Promise<MfaSettings> {
  const { data } = await api.get<MfaSettings>("/auth/mfa/settings");
  return data;
}

export async function dismissMfaPrompt(): Promise<void> {
  await api.post("/auth/mfa/settings/dismiss");
}

export async function startAccountMfaEnrollment(password: string): Promise<MfaSettingsEnrollment> {
  const { data } = await api.post<MfaSettingsEnrollment>(
    "/auth/mfa/settings/enroll/start",
    { password },
    withoutRefresh(),
  );
  return data;
}

export async function confirmAccountMfaEnrollment(payload: MfaCodeRequest): Promise<TokenPair> {
  const { data } = await api.post<TokenPair>(
    "/auth/mfa/settings/enroll/confirm",
    payload,
    withoutRefresh(),
  );
  return data;
}

export async function disableAccountMfa(payload: MfaDisableRequest): Promise<TokenPair> {
  const { data } = await api.post<TokenPair>(
    "/auth/mfa/settings/disable",
    payload,
    withoutRefresh(),
  );
  return data;
}

export async function requestPasswordSetupCode(): Promise<LoginCodeResponse> {
  const { data } = await api.post<LoginCodeResponse>(
    "/auth/password/setup/code",
    {},
    withoutRefresh(),
  );
  return data;
}

export async function setupAccountPassword(payload: {
  code: string;
  new_password: string;
}): Promise<void> {
  await api.post("/auth/password/setup", payload, withoutRefresh());
}

export async function confirmAccountPassword(password: string): Promise<TokenPair> {
  const { data } = await api.post<TokenPair>(
    "/auth/password/confirm",
    { password },
    withoutStepUp(),
  );
  return data;
}
