import { CanceledError } from "axios";

import { api, withoutAuth } from "@/lib/api";
import { getAuthSessionGeneration } from "@/stores/auth";

import {
  confirmLogout,
  getAuthEpoch,
  isSessionRestorationBlocked,
  markExplicitLogin,
  readLogoutState,
} from "./logoutState";
import { rememberTokenEpoch } from "./tokenEpoch";

const COOKIE_LOCK = "aurum:auth-cookie";
let fallbackQueue: Promise<unknown> = Promise.resolve();

async function withCookieLock<T>(execute: () => Promise<T>): Promise<T> {
  if (navigator.locks?.request) return await navigator.locks.request(COOKIE_LOCK, execute);
  // An unsupported browser must not reopen a previously closed cookie session.
  if (isSessionRestorationBlocked()) {
    return Promise.reject(
      new Error("Для безопасного входа откройте приложение или обновлённый браузер через HTTPS."),
    );
  }
  const pending = fallbackQueue.then(execute);
  fallbackQueue = pending.catch(() => undefined);
  return pending;
}

async function finishLogoutUnderLock(): Promise<void> {
  const intent = readLogoutState();
  if (intent?.phase !== "pending") return;
  await api.post(
    "/auth/logout",
    intent.operationId ? { operation_id: intent.operationId } : null,
    withoutAuth({ timeout: 10_000 }),
  );
  confirmLogout(intent.id);
}

export function completePendingLogout(): Promise<void> {
  return withCookieLock(finishLogoutUnderLock);
}

/** Serialize Set-Cookie responses across tabs, including late refresh responses. */
export function runCookieRequest<T>(
  kind: "refresh" | "explicit" | "account",
  execute: () => Promise<T>,
): Promise<T> {
  const epoch = getAuthEpoch();
  const generation = getAuthSessionGeneration();
  const ensureCurrent = () => {
    if (epoch !== getAuthEpoch() || generation !== getAuthSessionGeneration()) {
      throw new CanceledError("Authentication session changed");
    }
  };
  return withCookieLock(async () => {
    ensureCurrent();
    if (kind === "explicit") {
      if (epoch === "blocked") {
        throw new Error("Браузер не может безопасно сохранить состояние входа.");
      }
      await finishLogoutUnderLock();
      ensureCurrent();
    } else if (isSessionRestorationBlocked()) {
      throw new CanceledError("Authentication session is closed");
    }
    const result = await execute();
    ensureCurrent();
    let acceptedEpoch = epoch;
    if (
      kind === "explicit" &&
      typeof result === "object" &&
      result !== null &&
      "access_token" in result &&
      !markExplicitLogin(epoch, (next) => {
        acceptedEpoch = next;
      })
    ) {
      throw new CanceledError("Authentication session changed");
    }
    return rememberTokenEpoch(result, acceptedEpoch);
  });
}

/** Access-only proofs must not acquire a lock already held by an MFA action. */
export async function runSessionRequest<T>(execute: () => Promise<T>): Promise<T> {
  const epoch = getAuthEpoch();
  const generation = getAuthSessionGeneration();
  if (isSessionRestorationBlocked()) throw new CanceledError("Authentication session is closed");
  const result = await execute();
  if (epoch !== getAuthEpoch() || generation !== getAuthSessionGeneration()) {
    throw new CanceledError("Authentication session changed");
  }
  return rememberTokenEpoch(result, epoch);
}
