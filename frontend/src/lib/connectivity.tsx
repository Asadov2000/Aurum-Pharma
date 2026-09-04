import { onlineManager } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import {
  ConnectivityContext,
  readNavigatorOnline,
  type ConnectivityState,
  type ConnectivityStatus,
} from "@/lib/connectivityContext";
import { checkServerHealth } from "@/lib/serverHealth";

const DEFAULT_HEALTH_POLL_MS = 30_000;
const DEFAULT_FAILURE_RETRY_MS = 1_500;
const DEFAULT_FAILURE_THRESHOLD = 2;
const DEFAULT_RECOVERY_POLL_MS = 10_000;

interface ConnectivityProviderProps {
  readonly children: ReactNode;
  readonly checkHealth?: () => Promise<boolean>;
}

export function ConnectivityProvider({
  children,
  checkHealth = checkServerHealth,
}: ConnectivityProviderProps): JSX.Element {
  const [status, setStatus] = useState<ConnectivityStatus>(() =>
    readNavigatorOnline() ? "checking" : "offline",
  );
  const requestSequence = useRef(0);
  const consecutiveFailures = useRef(0);
  const requestState = useRef<0 | 1 | 2>(0);
  const retryTimer = useRef<number | null>(null);
  const runCheckRef = useRef<() => void>(() => undefined);

  const transitionTo = useCallback((nextStatus: ConnectivityStatus) => {
    setStatus(nextStatus);
    onlineManager.setOnline(nextStatus === "online" || nextStatus === "checking");
  }, []);

  const scheduleCheck = useCallback((delayMs?: number) => {
    if (retryTimer.current !== null) {
      window.clearTimeout(retryTimer.current);
      retryTimer.current = null;
    }
    if (delayMs !== undefined) {
      retryTimer.current = window.setTimeout(() => {
        retryTimer.current = null;
        runCheckRef.current();
      }, delayMs);
    }
  }, []);

  const runCheck = useCallback(() => {
    scheduleCheck();
    if (!readNavigatorOnline()) {
      requestSequence.current += 1;
      consecutiveFailures.current = 0;
      requestState.current = 0;
      transitionTo("offline");
      return;
    }
    if (requestState.current !== 0) {
      requestState.current = 2;
      return;
    }

    const sequence = ++requestSequence.current;
    requestState.current = 1;
    const applyResult = (healthy: boolean) => {
      if (requestSequence.current !== sequence) {
        return;
      }
      const retryImmediately = requestState.current === 2;
      requestState.current = 0;
      if (!readNavigatorOnline()) {
        consecutiveFailures.current = 0;
        transitionTo("offline");
        return;
      }
      if (healthy) {
        consecutiveFailures.current = 0;
        transitionTo("online");
        scheduleCheck(DEFAULT_HEALTH_POLL_MS);
        return;
      }

      consecutiveFailures.current += 1;
      if (consecutiveFailures.current >= DEFAULT_FAILURE_THRESHOLD) {
        transitionTo("server-unavailable");
        scheduleCheck(retryImmediately ? 0 : DEFAULT_RECOVERY_POLL_MS);
        return;
      }

      transitionTo("checking");
      scheduleCheck(retryImmediately ? 0 : DEFAULT_FAILURE_RETRY_MS);
    };
    void checkHealth().then(applyResult, () => applyResult(false));
  }, [checkHealth, scheduleCheck, transitionTo]);
  runCheckRef.current = runCheck;

  useEffect(() => {
    runCheck();
    const recheckEvents = ["online", "offline", "focus"] as const;
    const checkWhenVisible = () => {
      if (document.visibilityState === "visible") {
        runCheck();
      }
    };
    recheckEvents.forEach((event) => window.addEventListener(event, runCheck));
    document.addEventListener("visibilitychange", checkWhenVisible);

    return () => {
      requestSequence.current += 1;
      requestState.current = 0;
      scheduleCheck();
      recheckEvents.forEach((event) => window.removeEventListener(event, runCheck));
      document.removeEventListener("visibilitychange", checkWhenVisible);
    };
  }, [runCheck, scheduleCheck]);

  const value: ConnectivityState = {
    status,
    canUseServer: status === "online" || status === "checking",
    checkNow: runCheck,
  };

  return <ConnectivityContext.Provider value={value}>{children}</ConnectivityContext.Provider>;
}
