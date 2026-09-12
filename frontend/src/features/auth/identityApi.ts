import { api, withoutAuth, withoutRefresh } from "@/lib/api";
import { type LoginCodeResponse, type LoginVerifyResponse } from "./types";

export type ContactChannel = "email" | "phone";

export interface AccountIdentity {
  phone: string | null;
  phone_verified: boolean;
  has_password: boolean;
  password_prompt_pending: boolean;
}

const anonymousConfig = (signal?: AbortSignal) => withoutAuth({ signal, timeout: 30_000 });

export async function fetchLoginMethods(): Promise<{ phone_login_enabled: boolean }> {
  const { data } = await api.get<{ phone_login_enabled: boolean }>(
    "/auth/methods",
    anonymousConfig(),
  );
  return data;
}

export async function loginWithPassword(
  payload: { email: string; password: string },
  signal?: AbortSignal,
): Promise<LoginVerifyResponse> {
  const { data } = await api.post<LoginVerifyResponse>(
    "/auth/login/password",
    payload,
    anonymousConfig(signal),
  );
  return data;
}

export async function requestPhoneLoginCode(
  phone: string,
  signal?: AbortSignal,
): Promise<LoginCodeResponse> {
  const { data } = await api.post<LoginCodeResponse>(
    "/auth/login/phone/code",
    { phone },
    anonymousConfig(signal),
  );
  return data;
}

export async function verifyPhoneLoginCode(
  payload: { phone: string; code: string },
  signal?: AbortSignal,
): Promise<LoginVerifyResponse> {
  const { data } = await api.post<LoginVerifyResponse>(
    "/auth/login/phone/verify",
    payload,
    anonymousConfig(signal),
  );
  return data;
}

export async function requestPasswordResetCode(
  payload: { channel: ContactChannel; contact: string },
  signal?: AbortSignal,
): Promise<LoginCodeResponse> {
  const { data } = await api.post<LoginCodeResponse>(
    "/auth/password/reset/code",
    payload,
    anonymousConfig(signal),
  );
  return data;
}

export async function resetAccountPassword(
  payload: {
    channel: ContactChannel;
    contact: string;
    code: string;
    new_password: string;
    mfa_code?: string;
    recovery_code?: string;
  },
  signal?: AbortSignal,
): Promise<void> {
  await api.post("/auth/password/reset", payload, anonymousConfig(signal));
}

export async function fetchAccountIdentity(): Promise<AccountIdentity> {
  const { data } = await api.get<AccountIdentity>("/auth/identity");
  return data;
}

export async function dismissPasswordPrompt(): Promise<void> {
  await api.post("/auth/password/prompt/dismiss", {}, withoutRefresh());
}

export async function requestPhoneReauthenticationCode(): Promise<LoginCodeResponse> {
  const { data } = await api.post<LoginCodeResponse>(
    "/auth/phone/reauth/code",
    {},
    withoutRefresh(),
  );
  return data;
}

export async function requestPhoneChangeCode(payload: {
  phone: string;
  password?: string;
  reauth_code?: string;
  mfa_code?: string;
}): Promise<LoginCodeResponse> {
  const { data } = await api.post<LoginCodeResponse>(
    "/auth/phone/code",
    payload,
    withoutRefresh({ timeout: 30_000 }),
  );
  return data;
}

export async function verifyPhoneChangeCode(payload: {
  phone: string;
  code: string;
}): Promise<void> {
  await api.post("/auth/phone/verify", payload, withoutRefresh());
}
