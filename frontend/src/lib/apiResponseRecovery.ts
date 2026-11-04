import axios, { CanceledError, type AxiosError, type AxiosResponse } from "axios";

import type { FreshAccessRecovery, RetryConfig } from "./api";

type CurrentRequest = RetryConfig & { _auth: NonNullable<RetryConfig["_auth"]> };

interface RecoveryContext {
  isCurrent: (config: RetryConfig) => boolean;
  isStepUpRequired: (error: AxiosError) => boolean;
  refresh: (generation: number, epoch: string | null) => Promise<string | null>;
  stepUp: (generation: number, epoch: string | null) => Promise<string | null>;
  request: (config: RetryConfig) => Promise<AxiosResponse<unknown>>;
  authFailure: () => void;
  recover: FreshAccessRecovery | null;
}

const RECOVERED_REQUEST_TIMEOUT_MS = 30_000;

// No session state is imported here. Callbacks bind the originating client's
// current authority, even when this chunk finishes loading after an account change.
export async function recoverApiResponse(
  original: CurrentRequest,
  error: AxiosError,
  context: RecoveryContext,
): Promise<AxiosResponse<unknown>> {
  if (!context.isCurrent(original)) throw error;
  const assertActive = () => {
    if (!context.isCurrent(original)) {
      throw new CanceledError("Authentication session changed", original);
    }
    if (original.signal?.aborted) throw new CanceledError("Request canceled", original);
  };
  const retryAfterStepUp = async (denial: AxiosError) => {
    const ensureStepUpRequest = () => {
      // Preserve the original denial on identity/support replacement.
      if (!context.isCurrent(original)) throw denial;
      if (original.signal?.aborted) throw new CanceledError("Request canceled", original);
    };
    ensureStepUpRequest();
    original._stepUpRetry = true;
    const token = await context.stepUp(original._auth.generation, original._auth.epoch);
    ensureStepUpRequest();
    if (!token) throw denial;
    original.headers.set("Authorization", `Bearer ${token}`);
    return context.request(original);
  };
  if (
    !original._skipAuth &&
    !original._stepUpRetry &&
    !original._deferStepUp &&
    context.isStepUpRequired(error)
  ) {
    return retryAfterStepUp(error);
  }
  if (
    original._skipAuth ||
    original._skipRefresh ||
    original._retry ||
    error.response?.status !== 401
  ) {
    throw error;
  }
  original._retry = true;
  if (context.recover) {
    try {
      assertActive();
      const response = await context.recover(async (token) => {
        assertActive();
        // A step-up denial must leave the lock before opening a user dialog.
        original._deferStepUp = true;
        // Axios cancels a stalled retry; it must not hold the origin lock forever.
        original.timeout =
          typeof original.timeout === "number" &&
          Number.isFinite(original.timeout) &&
          original.timeout > 0
            ? Math.min(original.timeout, RECOVERED_REQUEST_TIMEOUT_MS)
            : RECOVERED_REQUEST_TIMEOUT_MS;
        original.headers.set("Authorization", `Bearer ${token}`);
        return context.request(original);
      }, assertActive);
      if (response) return response;
      if (context.isCurrent(original)) context.authFailure();
      throw error;
    } catch (recoveryError) {
      if (
        axios.isAxiosError(recoveryError) &&
        context.isCurrent(original) &&
        !original._stepUpRetry &&
        context.isStepUpRequired(recoveryError)
      ) {
        delete original._deferStepUp;
        return retryAfterStepUp(recoveryError);
      }
      throw recoveryError;
    } finally {
      delete original._deferStepUp;
    }
  }
  // Low-level injected clients retain the existing single-flight primitive.
  const token = await context.refresh(original._auth.generation, original._auth.epoch);
  if (!token || !context.isCurrent(original)) throw error;
  original.headers.set("Authorization", `Bearer ${token}`);
  return context.request(original);
}
