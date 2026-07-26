import { useCallback, useEffect, useState } from "react";

import { checkServerHealth } from "@/lib/serverHealth";

const DEFAULT_HEALTH_POLL_MS = 30_000;

type ServerStatus = "unknown" | "available" | "unavailable";

interface ServerStatusBannerProps {
  readonly checkHealth?: (signal: AbortSignal) => Promise<boolean>;
  readonly getOnline?: () => boolean;
  readonly pollMs?: number;
}

export function ServerStatusBanner({
  checkHealth = defaultCheckHealth,
  getOnline = getNavigatorOnline,
  pollMs = DEFAULT_HEALTH_POLL_MS,
}: ServerStatusBannerProps): JSX.Element | null {
  const [isOnline, setIsOnline] = useState(() => getOnline());
  const [status, setStatus] = useState<ServerStatus>("unknown");

  const runCheck = useCallback(
    async (signal: AbortSignal) => {
      if (!getOnline()) {
        setIsOnline(false);
        setStatus("unknown");
        return;
      }

      setIsOnline(true);
      let isHealthy: boolean;
      try {
        isHealthy = await checkHealth(signal);
      } catch {
        isHealthy = false;
      }

      if (signal.aborted) {
        return;
      }

      setStatus(isHealthy ? "available" : "unavailable");
    },
    [checkHealth, getOnline],
  );

  useEffect(() => {
    const controller = new AbortController();
    const handleConnectivityChange = () => {
      void runCheck(controller.signal);
    };

    void runCheck(controller.signal);

    const interval = window.setInterval(() => {
      void runCheck(controller.signal);
    }, pollMs);

    window.addEventListener("online", handleConnectivityChange);
    window.addEventListener("offline", handleConnectivityChange);
    window.addEventListener("focus", handleConnectivityChange);

    return () => {
      controller.abort();
      window.clearInterval(interval);
      window.removeEventListener("online", handleConnectivityChange);
      window.removeEventListener("offline", handleConnectivityChange);
      window.removeEventListener("focus", handleConnectivityChange);
    };
  }, [pollMs, runCheck]);

  if (!isOnline || status !== "unavailable") {
    return null;
  }

  return (
    <div
      aria-live="polite"
      className="border-b border-destructive/30 bg-destructive/10 px-4 py-2 text-sm font-semibold text-destructive sm:px-6"
      data-testid="server-status-banner"
      role="status"
    >
      Сервер недоступен. Интернет есть, но API не отвечает; кассовые операции
      временно заблокированы.
    </div>
  );
}

function defaultCheckHealth(signal: AbortSignal): Promise<boolean> {
  return checkServerHealth({ signal });
}

function getNavigatorOnline(): boolean {
  return typeof navigator === "undefined" ? true : navigator.onLine;
}
