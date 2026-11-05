import { api, withoutAuth, withoutRefresh } from "@/lib/api";
import { runCookieRequest, runSessionRequest } from "./cookieRequests";

import {
  type LoginCodeRequest,
  type LoginCodeResponse,
  type LoginVerifyResponse,
  type LoginVerifyRequest,
  type MeResponse,
  type TokenPair,
} from "./types";

export const AUTH_REQUEST_TIMEOUT_MS = 30_000;

export async function requestLoginCode(
  payload: LoginCodeRequest,
  signal?: AbortSignal,
): Promise<LoginCodeResponse> {
  const { data } = await api.post<LoginCodeResponse>(
    "/auth/login/code",
    payload,
    withoutAuth({ signal, timeout: AUTH_REQUEST_TIMEOUT_MS }),
  );
  return data;
}

export async function verifyLoginCode(
  payload: LoginVerifyRequest,
  signal?: AbortSignal,
): Promise<LoginVerifyResponse> {
  return runCookieRequest("explicit", async () => {
    const { data } = await api.post<LoginVerifyResponse>(
      "/auth/login/verify",
      payload,
      withoutAuth({ signal, timeout: AUTH_REQUEST_TIMEOUT_MS }),
    );
    return data;
  });
}

export async function stepUpMfa(code: string): Promise<TokenPair> {
  return runSessionRequest(async () => {
    const { data } = await api.post<TokenPair>("/auth/mfa/step-up", { code }, withoutRefresh());
    return data;
  });
}

export async function refreshTokensRequest(operationId: string): Promise<TokenPair> {
  return runCookieRequest("refresh", async () => {
    const { data } = await api.post<TokenPair>(
      "/auth/refresh",
      { operation_id: operationId },
      withoutAuth({ timeout: AUTH_REQUEST_TIMEOUT_MS }),
    );
    return data;
  });
}

export async function fetchMe(signal?: AbortSignal): Promise<MeResponse> {
  const { data } = await api.get<MeResponse>("/auth/me", { signal });
  return data;
}
