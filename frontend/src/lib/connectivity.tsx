import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import {
  ConnectivityContext,
  readNavigatorOnline,
  type ConnectivityState,
  type ConnectivityStatus,
} from "@/lib/connectivityContext";
import { checkServerHealth } from "@/lib/serverHealth";

const DEFAULT_HEALTH_POLL_MS = 30_000;

interface ConnectivityProviderProps {
  readonly children: ReactNode;
  readonly checkHealth?: () => Promise<boolean>;
  readonly getOnline?: () => boolean;
  readonly pollMs?: number;
}

export function ConnectivityProvider({
  children,
  checkHealth = defaultCheckHealth,
  getOnline = readNavigatorOnline,
  pollMs = DEFAULT_HEALTH_POLL_MS,
}: ConnectivityProviderProps): JSX.Element {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<ConnectivityStatus>(() =>
    getOnline() ? "checking" : "offline",
  );
  const requestSequence = useRef(0);
  const previousStatus = useRef(status);

  const runCheck = useCallback(() => {
    if (!getOnline()) {
      requestSequence.current += 1;
      setStatus("offline");
      return;
    }

    const sequence = requestSequence.current + 1;
    requestSequence.current = sequence;
    void checkHealth()
      .then((healthy) => {
        if (requestSequence.current === sequence) {
          setStatus(healthy ? "online" : "server-unavailable");
        }
      })
      .catch(() => {
        if (requestSequence.current === sequence) {
          setStatus("server-unavailable");
        }
      });
  }, [checkHealth, getOnline]);

  useEffect(() => {
    runCheck();
    const interval = window.setInterval(runCheck, pollMs);
    window.addEventListener("online", runCheck);
    window.addEventListener("offline", runCheck);
    window.addEventListener("focus", runCheck);

    return () => {
      requestSequence.current += 1;
      window.clearInterval(interval);
      window.removeEventListener("online", runCheck);
      window.removeEventListener("offline", runCheck);
      window.removeEventListener("focus", runCheck);
    };
  }, [pollMs, runCheck]);

  useEffect(() => {
    if (previousStatus.current === "server-unavailable" && status === "online") {
      void queryClient.refetchQueries({ type: "active" }).catch(() => undefined);
    }
    previousStatus.current = status;
  }, [queryClient, status]);

  const value: ConnectivityState = {
    status,
    canUseServer: status === "online" || status === "checking",
    checkNow: runCheck,
  };

  return <ConnectivityContext.Provider value={value}>{children}</ConnectivityContext.Provider>;
}

function defaultCheckHealth(): Promise<boolean> {
  return checkServerHealth();
}
