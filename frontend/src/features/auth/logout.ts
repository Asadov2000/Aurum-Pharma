import { completePendingLogout } from "./cookieRequests";
import { beginLogout, getAuthEpoch, subscribeLogoutState } from "./logoutState";
import { getPendingRefreshOperationId } from "./refreshOperation";
import { clearClientSession } from "./session";

let pending: Promise<void> | null = null;
let stopListening: (() => void) | null = null;
let observedEpoch: string | null = null;

export function synchronizeLogoutState(): void {
  const next = getAuthEpoch();
  if (next === observedEpoch) return;
  observedEpoch = next;
  clearClientSession();
}

export function installLogoutSynchronization(): void {
  if (stopListening) return;
  observedEpoch = getAuthEpoch();
  stopListening = subscribeLogoutState(synchronizeLogoutState);
}

export function logoutLocally(): Promise<void> {
  if (pending) return pending;
  const operationId = getPendingRefreshOperationId();
  try {
    beginLogout(operationId);
  } finally {
    clearClientSession();
  }
  // A failure leaves a durable pending intent; never reopen the old session.
  const attempt = completePendingLogout().catch(() => undefined);
  const completion = attempt.finally(() => {
    if (pending === completion) pending = null;
  });
  pending = completion;
  return completion;
}
