import { api, withoutAuth, withoutRefresh } from "@/lib/api";
import { runCookieRequest, runSessionRequest } from "./cookieRequests";

import {
  type ActiveSession,
  type LoginCodeRequest,
  type LoginCodeResponse,
  type LoginVerifyResponse,
  type LoginVerifyRequest,
  type MeResponse,
  type MfaChallengeRequest,
  type MfaChallengeResponse,
  type MfaCodeRequest,
  type MfaEnrollmentSetup,
  type MfaRecoveryRequest,
  type SessionListResponse,
  type SessionRevokeResponse,
  type TokenPair,
} from "./types";

const AUTH_REQUEST_TIMEOUT_MS = 30_000;

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

export async function startMfaEnrollment(
  payload: MfaChallengeRequest,
  signal?: AbortSignal,
): Promise<MfaEnrollmentSetup> {
  const { data } = await api.post<MfaEnrollmentSetup>(
    "/auth/mfa/enroll/start",
    payload,
    withoutAuth({ signal, timeout: AUTH_REQUEST_TIMEOUT_MS }),
  );
  return data;
}

export async function completeMfaEnrollment(
  payload: MfaCodeRequest,
  signal?: AbortSignal,
): Promise<TokenPair> {
  return runCookieRequest("explicit", async () => {
    const { data } = await api.post<TokenPair>(
      "/auth/mfa/enroll/confirm",
      payload,
      withoutAuth({ signal, timeout: AUTH_REQUEST_TIMEOUT_MS }),
    );
    return data;
  });
}

export async function verifyMfa(payload: MfaCodeRequest, signal?: AbortSignal): Promise<TokenPair> {
  return runCookieRequest("explicit", async () => {
    const { data } = await api.post<TokenPair>(
      "/auth/mfa/verify",
      payload,
      withoutAuth({ signal, timeout: AUTH_REQUEST_TIMEOUT_MS }),
    );
    return data;
  });
}

export async function recoverMfa(
  payload: MfaRecoveryRequest,
  signal?: AbortSignal,
): Promise<MfaChallengeResponse> {
  const { data } = await api.post<MfaChallengeResponse>(
    "/auth/mfa/recover",
    payload,
    withoutAuth({ signal, timeout: AUTH_REQUEST_TIMEOUT_MS }),
  );
  return data;
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

export async function listActiveSessions(): Promise<ActiveSession[]> {
  const { data } = await api.get<SessionListResponse>("/auth/sessions");
  return data.items;
}

export async function revokeActiveSession(sessionId: string): Promise<SessionRevokeResponse> {
  const { data } = await api.delete<SessionRevokeResponse>(`/auth/sessions/${sessionId}`);
  return data;
}

export async function revokeOtherSessions(): Promise<SessionRevokeResponse> {
  const { data } = await api.post<SessionRevokeResponse>("/auth/sessions/revoke-others");
  return data;
}
