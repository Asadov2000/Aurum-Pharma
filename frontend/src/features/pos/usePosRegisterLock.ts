import { useEffect, useState } from "react";

const LOCK_PREFIX = "aurum:pos:register:";

export type PosRegisterLockStatus = "checking" | "owned" | "blocked" | "unsupported";

export interface PosRegisterLock {
  status: PosRegisterLockStatus;
  isOwner: boolean;
  message: string | null;
}

export function usePosRegisterLock(registerId: string): PosRegisterLock {
  const [status, setStatus] = useState<PosRegisterLockStatus>("checking");

  useEffect(() => {
    let disposed = false;
    setStatus("checking");

    const lockManager = navigator.locks;
    if (!lockManager) {
      setStatus("unsupported");
      return () => {
        disposed = true;
      };
    }

    const abortController = new AbortController();
    let releaseLock = (): void => undefined;
    const releasePromise = new Promise<void>((resolve) => {
      releaseLock = resolve;
    });
    const waitingTimer = window.setTimeout(() => {
      if (!disposed) setStatus("blocked");
    }, 150);

    void lockManager
      .request(
        `${LOCK_PREFIX}${registerId}`,
        { mode: "exclusive", signal: abortController.signal },
        async () => {
          window.clearTimeout(waitingTimer);
          if (disposed) return;
          setStatus("owned");
          await releasePromise;
        },
      )
      .catch(() => {
        if (!disposed && !abortController.signal.aborted) setStatus("unsupported");
      });

    return () => {
      window.clearTimeout(waitingTimer);
      disposed = true;
      abortController.abort();
      releaseLock();
    };
  }, [registerId]);

  return {
    status,
    isOwner: status === "owned",
    message:
      status === "checking"
        ? "Проверяем, свободна ли эта касса…"
        : status === "blocked"
          ? "Эта касса уже открыта в другой вкладке. Закройте её там или выберите другую кассу."
          : status === "unsupported"
            ? "Обновите браузер: эта версия не поддерживает безопасную блокировку кассы."
            : null,
  };
}
