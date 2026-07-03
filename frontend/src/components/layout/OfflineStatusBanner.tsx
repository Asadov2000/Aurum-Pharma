import { useEffect, useState } from "react";

export function OfflineStatusBanner(): JSX.Element | null {
  const [isOnline, setIsOnline] = useState(() => getNavigatorOnline());

  useEffect(() => {
    const update = () => setIsOnline(getNavigatorOnline());

    window.addEventListener("online", update);
    window.addEventListener("offline", update);

    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);

  if (isOnline) {
    return null;
  }

  return (
    <div
      aria-live="polite"
      className="border-b border-warning/30 bg-warning-subtle px-4 py-2 text-sm font-semibold text-warning-foreground sm:px-6"
      data-testid="offline-status-banner"
      role="status"
    >
      Нет связи. Касса работает только онлайн, операции будут доступны после
      восстановления интернета.
    </div>
  );
}

function getNavigatorOnline(): boolean {
  return typeof navigator === "undefined" ? true : navigator.onLine;
}
