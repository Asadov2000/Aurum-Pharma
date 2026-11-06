import { api, withoutAuth } from "@/lib/api";

import { AUTH_REQUEST_TIMEOUT_MS } from "./api";
import { runCookieRequest } from "./cookieRequests";
import {
  type MfaChallengeRequest,
  type MfaChallengeResponse,
  type MfaCodeRequest,
  type MfaEnrollmentSetup,
  type MfaRecoveryRequest,
  type TokenPair,
} from "./types";

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
