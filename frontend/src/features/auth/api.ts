import { api, withoutAuth } from "@/lib/api";

import {
  type LoginCodeRequest,
  type LoginCodeResponse,
  type LoginVerifyRequest,
  type MeResponse,
  type TokenPair,
} from "./types";

export async function requestLoginCode(
  payload: LoginCodeRequest,
): Promise<LoginCodeResponse> {
  const { data } = await api.post<LoginCodeResponse>(
    "/auth/login/code",
    payload,
    withoutAuth(),
  );
  return data;
}

export async function verifyLoginCode(payload: LoginVerifyRequest): Promise<TokenPair> {
  const { data } = await api.post<TokenPair>(
    "/auth/login/verify",
    payload,
    withoutAuth(),
  );
  return data;
}

export async function refreshTokensRequest(refreshToken: string): Promise<TokenPair> {
  const { data } = await api.post<TokenPair>(
    "/auth/refresh",
    { refresh_token: refreshToken },
    withoutAuth(),
  );
  return data;
}

export async function logoutRequest(refreshToken: string): Promise<void> {
  await api.post(
    "/auth/logout",
    { refresh_token: refreshToken },
    withoutAuth(),
  );
}

export async function fetchMe(): Promise<MeResponse> {
  const { data } = await api.get<MeResponse>("/auth/me");
  return data;
}
